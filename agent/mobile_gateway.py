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
        if bundled.exists() and not target.exists():
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


def dashboard() -> Dict[str, Any]:
    """Return live portfolio snapshot & history log dynamically."""
    records = []
    path = Path(PERFORMANCE_FILE)
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            _logs.append(f"Dashboard read error: {exc}")

    live = _live_dashboard_record()
    if live:
        history = list(records[-500:]) if records else []
        if not history or history[-1].get("cycle") != live.get("cycle"):
            history.append(live)
        else:
            history[-1] = live
        return {
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

    if records:
        record = records[-1]
        return {
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

    return {
        "total": 0,
        "cash": 0,
        "invested": 0,
        "positions": 0,
        "signals": 0,
        "regime": "Awaiting first cycle",
        "regime_score": 0.0,
        "tier1_usd": 0,
        "tier2_usd": 0,
        "tier3_usd": 0,
        "positions_detail": [],
        "trade_stats": {},
        "history": [],
        "running": is_running(),
    }


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


def start_bot(budget: float | None = None) -> None:
    global _thread, _bot, _stop_event
    if _thread and _thread.is_alive():
        return
    _prepare_runtime()
    try:
        from .bot import MaxAlphaV4
        cap = budget or _previous_budget()
        _logs.append(f"Starting MaxAlpha with wallet cap Rs{cap:,.2f}")
        _stop_event = threading.Event()
        _bot = MaxAlphaV4(budget=cap, run_mode="discover", stop_event=_stop_event)
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
