"""On-device API for the Flutter Android bridge.

This module deliberately wraps the existing bot without changing its scoring,
risk, execution, or state logic. Android stores secrets in Keystore-backed
secure storage; they are passed to this process only when a run is requested.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from .settings import CONFIG, PERFORMANCE_FILE, PORTFOLIO_STATE_FILE, PROJECT_ROOT, log, project_path

_thread: threading.Thread | None = None
_logs: list[str] = []
_bot = None
_stop_event: threading.Event | None = None
_dashboard_server: ThreadingHTTPServer | None = None
_dashboard_thread: threading.Thread | None = None

# Dashboard response cache — avoids re-reading performance_v4.json on every poll
_dashboard_cache: Dict[str, Any] | None = None
_dashboard_cache_mtime: float = 0.0
_dashboard_cache_ts: float = 0.0
_CACHE_STALENESS_S: float = 30.0  # force refresh every 30 s even if file unchanged


class _MobileLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _logs.append(self.format(record))


def _prepare_runtime() -> None:
    """Use private app storage for files which the desktop bot reads by name."""
    runtime = Path(PROJECT_ROOT)
    os.chdir(runtime)
    for filename in ("symbol_map.json", "exit_rules.json", "dashboard_v5_bot2.html"):
        bundled = Path(__file__).with_name(filename)
        target = runtime / filename
        # Dashboard and official Dhan mappings are application assets, not
        # user state. Refresh them after an app update so mobile fixes reach
        # devices that already have a private runtime directory.
        if bundled.exists() and (filename in {"symbol_map.json", "dashboard_v5_bot2.html"} or not target.exists()):
            target.write_bytes(bundled.read_bytes())

    logger = logging.getLogger("MaxAlphaV4")
    if not any(isinstance(handler, _MobileLogHandler) for handler in logger.handlers):
        handler = _MobileLogHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)


def detect_ai_provider(api_key: str) -> Dict[str, str]:
    """Choose a compatible provider/model from a user supplied key prefix."""
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):
        return {"provider": "anthropic", "model": "claude-3-5-sonnet-latest"}
    if key.startswith("sk-proj-") or key.startswith("sk-"):
        return {"provider": "openai", "model": "gpt-4o-mini"}
    if key.startswith("gsk_"):
        return {"provider": "groq", "model": "llama-3.3-70b-versatile"}
    return {"provider": "openrouter", "model": "deepseek/deepseek-r1:free"}


def _as_python_dict(values: Any) -> Dict[str, Any]:
    """Chaquopy passes Flutter maps as java.util.HashMap, not dict."""
    if isinstance(values, dict):
        return values
    try:
        result: Dict[str, Any] = {}
        iterator = values.keySet().iterator()
        while iterator.hasNext():
            key = iterator.next()
            result[str(key)] = values.get(key)
        return result
    except AttributeError:
        return dict(values)


def configure(values: Dict[str, Any]) -> Dict[str, str]:
    """Apply credentials in memory. They are never written to .env by mobile."""
    values = _as_python_dict(values)
    _prepare_runtime()
    trading = values.get("tradingMode", values.get("trading_mode", "paper"))
    council = values.get("councilMode", values.get("council_mode", "local"))
    key = values.get("aiKey", values.get("ai_key", ""))
    provider = detect_ai_provider(key)
    CONFIG.update({
        "execution_mode": "signals_only" if trading == "paper" else "broker",
        "broker_paper": trading == "paper",
        "dhan_client_id": values.get("dhanClientId", values.get("dhan_client_id", "")),
        "dhan_access_token": values.get("dhanAccessToken", values.get("dhan_access_token", "")),
        "council_mode": {"ai": "openrouter", "local": "local", "dual": "dual"}.get(council, "local"),
        "local_council_only": council == "local",
        "mobile_ai_provider": provider["provider"],
        "mobile_ai_model": provider["model"],
        "mobile_ai_key": key,
        "openrouter_key": key if provider["provider"] == "openrouter" else "",
        "anthropic_key": key if provider["provider"] == "anthropic" else "",
        "groq_key": key if provider["provider"] == "groq" else "",
    })
    return provider


def _live_dashboard_record() -> Dict[str, Any] | None:
    """Expose the initialized bot portfolio before or during its active cycles.

    This is display-only: it reads the existing executor and never trades,
    scores, records, or mutates bot state.
    """
    if _bot is None:
        return None
    try:
        portfolio = _bot.executor.get_portfolio()
        tracker = _bot.tracker
        regime = _bot._regime_cache
        analyses = _bot.executor.analyze_positions(portfolio) if hasattr(_bot.executor, "analyze_positions") else []
        stats = _bot.executor.trade_stats() if hasattr(_bot.executor, "trade_stats") else {}
        return {
            "cycle": getattr(tracker, "cycle", 1) if tracker else 1,
            "ts": datetime.now().astimezone().isoformat(),
            "total_usd": round(portfolio.total_usd, 2),
            "cash_usd": round(portfolio.cash_usd, 2),
            "wallet_cap": round(getattr(_bot.executor, "_initial_budget", portfolio.total_usd), 2),
            "invested_usd": round(portfolio.invested_usd, 2),
            "positions": portfolio.open_count,
            "tier1_usd": round(portfolio.tier1_value, 2),
            "tier2_usd": round(portfolio.tier2_value, 2),
            "tier3_usd": round(portfolio.tier3_value, 2),
            "regime": regime.state if regime else "Starting",
            "regime_score": regime.score if regime else 0.0,
            "signals": 0,
            "positions_detail": analyses,
            "trade_stats": stats,
        }
    except Exception as exc:
        _logs.append(f"Live dashboard read error: {exc}")
        return None


def _fallback_record() -> Dict[str, Any]:
    """Read saved portfolio_state_v4.json or budget fallback when bot cycle hasn't recorded yet."""
    budget = _previous_budget()
    cash = budget
    invested = 0.0
    positions = 0
    positions_detail = []
    trade_stats = {"wins": 0, "losses": 0, "closed_trades": [], "order_history": []}
    state_path = Path(PORTFOLIO_STATE_FILE)
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            initial_budget = float(state.get("initial_budget") or budget)
            cash = float(state.get("cash_usd") or initial_budget)
            raw_pos = state.get("positions", {})
            if isinstance(raw_pos, dict):
                positions = len(raw_pos)
                for tkr, p in raw_pos.items():
                    if isinstance(p, dict):
                        qty = float(p.get("qty") or 0)
                        cp = float(p.get("current_price") or p.get("avg_cost") or 0)
                        bp = float(p.get("avg_cost") or 0)
                        mv = float(p.get("market_value") or (qty * cp))
                        invested += mv
                        positions_detail.append({
                            "ticker": tkr,
                            "tier": p.get("tier", 1),
                            "qty": qty,
                            "buy_price": bp,
                            "current_price": cp,
                            "pnl_pct": round(((cp - bp) / bp * 100) if bp > 0 else 0, 2),
                            "pnl_rs": round((cp - bp) * qty, 2),
                        })
            trade_stats = {
                "wins": state.get("wins", 0),
                "losses": state.get("losses", 0),
                "closed_trades": state.get("closed_trades", []),
                "order_history": state.get("order_history", []),
            }
            budget = initial_budget
        except Exception as exc:
            _logs.append(f"Fallback state read error: {exc}")

    total = cash + invested
    return {
        "cycle": 0,
        "ts": datetime.now().astimezone().isoformat(),
        "total_usd": round(total, 2),
        "cash_usd": round(cash, 2),
        "wallet_cap": round(budget, 2),
        "invested_usd": round(invested, 2),
        "positions": positions,
        "tier1_usd": 0.0,
        "tier2_usd": 0.0,
        "tier3_usd": 0.0,
        "regime": "Awaiting first cycle",
        "regime_score": 0.5,
        "signals": 0,
        "positions_detail": positions_detail,
        "trade_stats": trade_stats,
    }


def _sync_performance_file(record: Dict[str, Any]) -> None:
    """Ensure performance_v4.json exists on disk for HTTP webview & history."""
    try:
        path = Path(PERFORMANCE_FILE)
        records = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                records = []
        if not records or records[-1].get("cycle") != record.get("cycle"):
            records.append(record)
        else:
            records[-1] = record
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    except Exception as exc:
        _logs.append(f"Performance sync error: {exc}")


def _invalidate_dashboard_cache() -> None:
    """Call this whenever the bot writes a new cycle so the next poll sees fresh data."""
    global _dashboard_cache, _dashboard_cache_mtime, _dashboard_cache_ts
    _dashboard_cache = None
    _dashboard_cache_mtime = 0.0
    _dashboard_cache_ts = 0.0


def dashboard() -> Dict[str, Any]:
    """Return live portfolio snapshot & history log dynamically.

    Caches the response and only rebuilds it when performance_v4.json has
    actually changed on disk (mtime check) or after _CACHE_STALENESS_S seconds.
    While the bot is running, live data always bypasses the cache.
    """
    import time
    global _dashboard_cache, _dashboard_cache_mtime, _dashboard_cache_ts

    _prepare_runtime()

    # While the bot is actively running, always return fresh live data
    live = _live_dashboard_record()
    if live:
        _sync_performance_file(live)
        path = Path(PERFORMANCE_FILE)
        records: list = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                records = []
        history = list(records[-500:]) if records else []
        if not history or history[-1].get("cycle") != live.get("cycle"):
            history.append(live)
        else:
            history[-1] = live
        result = {
            "total": live["total_usd"],
            "cash": live["cash_usd"],
            "invested": live.get("invested_usd", 0),
            "positions": live["positions"],
            "signals": live.get("signals", 0),
            "regime": live["regime"],
            "regime_score": live.get("regime_score", 0.0),
            "tier1_usd": live.get("tier1_usd", 0),
            "tier2_usd": live.get("tier2_usd", 0),
            "tier3_usd": live.get("tier3_usd", 0),
            "positions_detail": live.get("positions_detail", []),
            "trade_stats": live.get("trade_stats", {}),
            "history": history,
            "running": True,
        }
        _dashboard_cache = result
        _dashboard_cache_ts = time.monotonic()
        return result

    # Bot is idle — use cached result if the JSON file hasn't changed
    path = Path(PERFORMANCE_FILE)
    now = time.monotonic()
    current_mtime = path.stat().st_mtime if path.exists() else 0.0
    cache_age = now - _dashboard_cache_ts
    if (
        _dashboard_cache is not None
        and current_mtime == _dashboard_cache_mtime
        and cache_age < _CACHE_STALENESS_S
    ):
        # File unchanged and cache is fresh — return cached result with updated running flag
        cached = dict(_dashboard_cache)
        cached["running"] = is_running()
        return cached

    # Cache miss: read file and build response
    records = []
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            _logs.append(f"Dashboard read error: {exc}")

    if records:
        record = records[-1]
        result = {
            "total": record.get("total_usd", 0),
            "cash": record.get("cash_usd", 0),
            "invested": record.get("invested_usd", 0),
            "positions": record.get("positions", 0),
            "signals": record.get("signals", 0),
            "regime": record.get("regime", "Awaiting first cycle"),
            "regime_score": record.get("regime_score", 0.0),
            "tier1_usd": record.get("tier1_usd", 0),
            "tier2_usd": record.get("tier2_usd", 0),
            "tier3_usd": record.get("tier3_usd", 0),
            "positions_detail": record.get("positions_detail", []),
            "trade_stats": record.get("trade_stats", {}),
            "history": records[-500:],
            "running": is_running(),
        }
        _dashboard_cache = result
        _dashboard_cache_mtime = current_mtime
        _dashboard_cache_ts = now
        return result

    # Fallback to portfolio_state_v4.json or initial budget
    fb = _fallback_record()
    _sync_performance_file(fb)
    result = {
        "total": fb["total_usd"],
        "cash": fb["cash_usd"],
        "invested": fb["invested_usd"],
        "positions": fb["positions"],
        "signals": 0,
        "regime": fb["regime"],
        "regime_score": fb["regime_score"],
        "tier1_usd": fb["tier1_usd"],
        "tier2_usd": fb["tier2_usd"],
        "tier3_usd": fb["tier3_usd"],
        "positions_detail": fb["positions_detail"],
        "trade_stats": fb["trade_stats"],
        "history": [fb],
        "running": is_running(),
    }
    _dashboard_cache = result
    _dashboard_cache_mtime = current_mtime
    _dashboard_cache_ts = now
    return result


def clear_history() -> Dict[str, Any]:
    """Delete performance_v4.json and paper_signals.json, then reset history."""
    _prepare_runtime()
    cleared_files = []
    for filename in ["performance_v4.json", "paper_signals.json"]:
        p = Path(filename)
        if p.exists():
            try:
                p.unlink()
                cleared_files.append(filename)
            except Exception as exc:
                _logs.append(f"Failed to delete {filename}: {exc}")
    _invalidate_dashboard_cache()
    _logs.append(f"Cleared bot history files: {cleared_files}")
    return {"ok": True, "cleared": cleared_files}


class _DashboardRequestHandler(SimpleHTTPRequestHandler):
    """Quiet local-only file server used by the mobile WebView."""

    def log_message(self, _format: str, *_args: Any) -> None:
        pass


def start_dashboard() -> Dict[str, Any]:
    """Serve the live dashboard and Python state on loopback only.

    This mirrors the desktop `python -m http.server` workflow, but uses the
    app's private runtime directory, which contains `performance_v4.json`.
    """
    global _dashboard_server, _dashboard_thread
    _prepare_runtime()
    if _dashboard_server and _dashboard_thread and _dashboard_thread.is_alive():
        return {"running": True, "url": "http://127.0.0.1:8765/dashboard_v5_bot2.html"}
    try:
        handler = partial(_DashboardRequestHandler, directory=str(Path(PROJECT_ROOT)))
        _dashboard_server = ThreadingHTTPServer(("127.0.0.1", 8765), handler)
        _dashboard_thread = threading.Thread(
            target=_dashboard_server.serve_forever,
            daemon=True,
            name="max-alpha-dashboard",
        )
        _dashboard_thread.start()
        _logs.append("Dashboard server started at http://127.0.0.1:8765/dashboard_v5_bot2.html")
        return {"running": True, "url": "http://127.0.0.1:8765/dashboard_v5_bot2.html"}
    except OSError as exc:
        _dashboard_server = None
        _dashboard_thread = None
        record_error(f"Dashboard server startup error: {exc}")
        raise


def _live_budget_cap(added_budget: float | None) -> float:
    """Apply the desktop live-wallet allocation rules without a terminal prompt."""
    from .bot import _get_dhan_balance, _load_live_budget_state, _save_live_budget_state

    previous_cap = max(0.0, float(_load_live_budget_state().get("cap") or 0.0))
    wallet_balance = max(0.0, _get_dhan_balance())
    added = max(0.0, float(added_budget or 0.0))
    cap = previous_cap if added_budget is None else previous_cap + added

    if cap <= 0:
        cap = wallet_balance if wallet_balance > 0 else float(CONFIG.get("trading_budget_default", 50000))
    if wallet_balance > 0 and cap > wallet_balance:
        _logs.append(
            f"Requested allocation Rs{cap:,.2f} exceeds Dhan wallet Rs{wallet_balance:,.2f}; "
            f"using Rs{wallet_balance:,.2f}."
        )
        cap = wallet_balance

    _save_live_budget_state(cap)
    _logs.append(
        f"Live Dhan wallet Rs{wallet_balance:,.2f}; bot allocation Rs{cap:,.2f}."
        if wallet_balance > 0 else
        f"Could not fetch Dhan wallet; bot allocation Rs{cap:,.2f}."
    )
    return cap


def start_bot(budget: float | None = None) -> None:
    global _thread, _bot, _stop_event
    if _thread and _thread.is_alive():
        return
    _prepare_runtime()
    try:
        from .bot import MaxAlphaV4
        is_live = CONFIG.get("execution_mode") == "broker" and not CONFIG.get("broker_paper", True)
        cap = _live_budget_cap(budget) if is_live else (budget or _previous_budget())
        _logs.append(f"Starting MaxAlpha with wallet cap Rs{cap:,.2f}")
        _stop_event = threading.Event()
        _bot = MaxAlphaV4(budget=cap, run_mode="discover", stop_event=_stop_event)
        live = _live_dashboard_record()
        if live:
            _sync_performance_file(live)
        else:
            _sync_performance_file(_fallback_record())
    except Exception as exc:
        record_error(f"MaxAlpha startup failed: {exc}")
        raise

    def run_safely() -> None:
        try:
            _bot.run()
        except BaseException as exc:
            record_error(f"MaxAlpha stopped unexpectedly: {exc}")

    _thread = threading.Thread(target=run_safely, daemon=True, name="max-alpha")
    _thread.start()


def stop_bot() -> None:
    """Request a safe stop at the next loop/sleep checkpoint."""
    if _stop_event:
        _stop_event.set()
        _logs.append("Stop requested; MaxAlpha will stop at its next checkpoint.")
    else:
        _logs.append("MaxAlpha is not running.")


def _previous_budget() -> float:
    try:
        return float(json.loads(Path(PORTFOLIO_STATE_FILE).read_text(encoding="utf-8-sig")).get("initial_budget", 50000))
    except Exception:
        return float(CONFIG.get("trading_budget_default", 50000))


def logs() -> list[str]:
    return _logs[-500:]


def record_error(message: str) -> None:
    _logs.append(message)
    log.error(message)


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def signals() -> str:
    path = Path(CONFIG["paper_signal_file"])
    return path.read_text(encoding="utf-8") if path.exists() else '{"signals": []}'
