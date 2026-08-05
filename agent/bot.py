import json
import os
import sys
import time
import traceback
from typing import List, Optional, Tuple

try:
    from .alpha import AlphaEngine, Tier1Scanner
    from .council import CouncilOfFive
    from .execution import Executor, SimExecutor
    from .models import MarketRegime, ScoredStock
    from .regime import RegimeEngine
    from .reporting import SignalExporter, Tracker
    from .scheduler import SmartScheduler
    from .settings import CONFIG, PORTFOLIO_STATE_FILE, TIER2_UNIVERSE, TIER3_UNIVERSE, log, project_path
    from .exit_manager import ExitManager
except ImportError:
    from alpha import AlphaEngine, Tier1Scanner
    from council import CouncilOfFive
    from execution import Executor, SimExecutor
    from models import MarketRegime, ScoredStock
    from regime import RegimeEngine
    from reporting import SignalExporter, Tracker
    from scheduler import SmartScheduler
    from settings import CONFIG, PORTFOLIO_STATE_FILE, TIER2_UNIVERSE, TIER3_UNIVERSE, log, project_path
    from exit_manager import ExitManager

class MaxAlphaV4:

    def __init__(self, mode: str = "full", budget: float = 1_000_000.0, run_mode: str = "discover", stop_event=None):
        self.bot_mode        = mode
        self.run_mode        = run_mode
        self.stop_event      = stop_event
        self.scanner         = Tier1Scanner()
        self.alpha           = AlphaEngine()
        self.regime_e        = RegimeEngine()
        self.council         = CouncilOfFive()
        self.execution_mode  = CONFIG["execution_mode"]
        is_live = (self.execution_mode == "broker" and not CONFIG["broker_paper"])
        self.executor        = SimExecutor(budget=budget) if self.execution_mode == "signals_only" else Executor(budget_cap=budget if is_live else 0.0)
        self.signal_exporter = SignalExporter(CONFIG["paper_signal_file"])
        self.scheduler       = SmartScheduler()
        self.tracker         = Tracker()
        self.exit_manager    = ExitManager()  # Smart exit system
        self._regime_cache: Optional[MarketRegime] = None
        self._regime_ts      = 0.0

    def run(self):
        log.info("MAX ALPHA v4 — Three-Tier Regime-Aware Agent")
        log.info(f"Mode: {'PAPER' if CONFIG['broker_paper'] else 'LIVE'}")
        log.info(f"Execution mode: {self.execution_mode}")
        log.info(f"Run mode: {self.run_mode}")
        log.info("Tiers: NSE momentum smallcaps | NSE midcaps | NSE bluechip/ETF")
        log.info(f"Trade goal: up to {CONFIG['desired_daily_trades']} quality trades/day, never forced")
        log.info("Capital: Dynamic — reads live Dhan balance every cycle")

        while True:
            if self.stop_event and self.stop_event.is_set():
                log.info("Stop event set — shutting down engine cleanly.")
                break
            try:
                secs, label, call_claude = self.scheduler.next_interval()
                log.info(f"[{self.scheduler.status()}] {label}")

                if not call_claude and secs > 60:
                    # Sleep  but still monitor existing positions
                    self._monitor_positions_only()
                    self._chunked_sleep(secs)
                    continue

                self._run_cycle(call_claude=call_claude)
                if secs > 0 and call_claude:
                    self._chunked_sleep(secs)

            except KeyboardInterrupt:
                log.info("Stopped.")
                break
            except Exception as e:
                log.error(f"Cycle error: {e}\n{traceback.format_exc()}")
                time.sleep(60)

    def _monitor_positions_only(self):
        """During off-hours: just watch stop-losses, no scanning."""
        try:
            portfolio = self.executor.get_portfolio()
            if portfolio.positions:
                self.executor.enforce_risk(portfolio)
        except Exception as e:
            log.debug(f"Position monitor error: {e}")

    def _chunked_sleep(self, secs: int):
        slept = 0
        while slept < secs:
            if self.stop_event and self.stop_event.is_set():
                break
            chunk = min(5, secs - slept)
            time.sleep(chunk)
            slept += chunk

    def _run_cycle(self, call_claude: bool = True):
        # â”€â”€ 1. Portfolio (always live, reflects any deposits) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        portfolio = self.executor.get_portfolio()
        log.info(f"  Portfolio: Rs{portfolio.total_usd:,.2f} INR | "
                 f"Cash: Rs{portfolio.cash_usd:,.2f} | "
                 f"{portfolio.open_count} positions")

        # â”€â”€ 2. Risk management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if portfolio.positions:
            self.executor.enforce_risk(portfolio)
            portfolio = self.executor.get_portfolio()

        # ── 3. Regime detection (cache for 30 min to save time) ───────────────
        now = time.time()
        if self._regime_cache is None or now - self._regime_ts > 1800:
            log.info("  Detecting market regime...")
            self._regime_cache = self.regime_e.detect()
            self._regime_ts    = now
            log.info(f"  Regime: {self._regime_cache.state} | "
                     f"Score:{self._regime_cache.score:.2f} | "
                     f"NIFTY {self._regime_cache.spy_trend} RSI:{self._regime_cache.spy_rsi:.0f} | "
                     f"IndiaVIX:{self._regime_cache.vix_level}")
            log.info(f"  Allocation: T1:{self._regime_cache.allocation['tier1']:.0%} "
                     f"T2:{self._regime_cache.allocation['tier2']:.0%} "
                     f"T3:{self._regime_cache.allocation['tier3']:.0%}")

        regime = self._regime_cache

        if not call_claude:
            log.info("  Pre-market scan complete  skipping Claude (market not open)")
            self.tracker.record(portfolio, regime, 0, self.executor)
            self.tracker.print_dashboard(portfolio, regime)
            return

        if hasattr(self.executor, "manage_existing_positions"):
            log.info("  Managing existing holdings before looking for new buys...")
            self.executor.manage_existing_positions(portfolio)
        
        # ── Smart Exit Check (NEW!) ─────────────────────────────────────────
        log.info("  Checking positions for smart exits...")
        self._check_smart_exits(portfolio, regime)
        self._check_stale_exits(portfolio)
        portfolio = self.executor.get_portfolio()

        # â”€â”€ 4. Scan candidates across all tiers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if hasattr(self.executor, "risk"):
            risk_status = self.executor.risk.status(portfolio)
            if not risk_status["can_trade"]:
                log.warning(f"  Risk manager halted new entries: {', '.join(risk_status['reasons'])}")
                self.tracker.record(portfolio, regime, 0, self.executor)
                self.tracker.print_dashboard(portfolio, regime)
                return

        all_candidates: List[ScoredStock] = []

        # Tier 1  only if regime allows it
        if regime.state != "CRASH" and regime.allocation["tier1"] > 0:
            log.info("  [T1] Scanning NSE Tier1 momentum basket...")
            t1_tickers = self.scanner.scan()
            t1_candidates = self._score_tickers(t1_tickers, tier=1, min_alpha=0.75)
            log.info(f"  [T1] {len(t1_candidates)} candidates (alpha >= 0.75)")
            all_candidates.extend(t1_candidates[:10])
        else:
            log.info(f"  [T1] Skipped  regime {regime.state} suppresses high-beta smallcaps")

        # Tier 2  small caps
        if regime.state not in ("CRASH",) and regime.allocation["tier2"] > 0:
            log.info("  [T2] Scoring broad mid/small-cap universe...")
            t2_tickers = self.scanner.scan_tier2() if CONFIG.get("broad_market_scan", True) else []
            if not t2_tickers:
                t2_tickers = TIER2_UNIVERSE
            t2_candidates = self._score_tickers(t2_tickers, tier=2, min_alpha=0.75)
            log.info(f"  [T2] {len(t2_candidates)} candidates (alpha >= 0.75)")
            all_candidates.extend(t2_candidates[:8])

        # Tier 3  blue chips & ETFs (always run)
        log.info("  [T3] Scoring broad large-cap / ETF universe...")
        t3_all = self.scanner.scan_tier3() if CONFIG.get("broad_market_scan", True) else []
        curated_t3 = [t for tickers in TIER3_UNIVERSE.values() for t in tickers]
        if not t3_all:
            t3_all = curated_t3
        else:
            t3_all = list(dict.fromkeys(t3_all + curated_t3))
        # In BEAR/CRASH, prioritise ETF hedges
        if regime.state in ("BEAR","CRASH"):
            t3_all = TIER3_UNIVERSE["etf_hedge"] + TIER3_UNIVERSE["etf_broad"] + \
                     TIER3_UNIVERSE["etf_sector"] + t3_all
        t3_candidates = self._score_tickers(t3_all, tier=3, min_alpha=0.65)
        log.info(f"  [T3] {len(t3_candidates)} candidates (alpha >= 0.65)")
        all_candidates.extend(t3_candidates[:8])

        # Sort all by alpha
        all_candidates.sort(key=lambda c: c.alpha_score, reverse=True)
        top = all_candidates[:18]

        if not top:
            log.info("  No candidates passed scoring  skipping Council")
            self.tracker.record(portfolio, regime, 0, self.executor)
            self.tracker.print_dashboard(portfolio, regime)
            return

        log.info(f"  Sending {len(top)} candidates to Council of Five...")
        for c in top[:6]:
            log.info(f"    T{c.tier} {c.ticker:6s} Rs{c.price:.4f} | "
                     f"alpha:{c.alpha_score:.2f} relVol:{c.rel_volume:.1f}x "
                     f"RSI:{c.rsi:.0f} {c.trend} | "
                     f"fund:{c.fundamental.overall_quality}/{c.fundamental.data_confidence}")

        # â”€â”€ 5. Council votes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        signals = self.council.vote(top, portfolio, regime)
        log.info(f"  Council approved {len(signals)} signals")
        for s in signals:
            log.info(f"    {s.action:4s} T{s.tier} {s.ticker:6s} | "
                     f"{s.consensus:.0%} | Rs{s.value_usd:.0f} | {s.strategy}")

        # â”€â”€ 6. Execute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        market_is_open = self.executor.market_open()
        if self.execution_mode == "signals_only":
            self.signal_exporter.export(
                signals=signals,
                regime=regime,
                market_open=market_is_open,
                cycle=self.tracker.cycle + 1,
            )
            log.info("  signals_only mode active - exporting latest signals to JSON")
            
        if signals and market_is_open:
            buys = sells = 0
            for signal in signals:
                is_add_to_existing = (
                    signal.action == "BUY"
                    and CONFIG.get("allow_add_to_existing_positions", False)
                    and signal.ticker in portfolio.positions
                )
                if (
                    signal.action == "BUY"
                    and not is_add_to_existing
                    and portfolio.open_count + buys >= CONFIG["max_open_positions"]
                ):
                    log.warning(f"  Max positions reached  skipping {signal.ticker}")
                    break
                if signal.action == "BUY":
                    if hasattr(self.executor, "risk") and not self.executor.risk.can_open(signal, portfolio):
                        continue
                    if self.executor.buy(signal):
                        buys += 1
                elif signal.action == "SELL":
                    if signal.ticker in portfolio.positions:
                        pos = portfolio.positions[signal.ticker]
                        if self.executor.sell(signal.ticker, pos.qty, f"Council SELL {signal.consensus:.0%}"):
                            sells += 1
            log.info(f"  Executed: {buys} buys, {sells} sells")
        elif not market_is_open:
            log.info("  Market closed  signals queued for next open")

        # â”€â”€ 7. Record & dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        portfolio = self.executor.get_portfolio()
        self.tracker.record(portfolio, regime, len(signals), self.executor)
        self.tracker.print_dashboard(portfolio, regime)

    def _score_tickers(self, tickers: List[str], tier: int,
                       min_alpha: float) -> List[ScoredStock]:
        results = []
        for ticker in tickers:
            c = self.alpha.score(ticker, tier)
            if c and tier >= 2:
                if c.strategy_setup == "orb_long_breakout":
                    pass
                elif c.fundamental.overall_quality == "WEAK":
                    log.debug(f"{ticker} tier{tier}: rejected by fundamental quality gate")
                    c = None
                elif c.fundamental.data_confidence == "VERY_LOW":
                    log.debug(f"{ticker} tier{tier}: rejected by fundamental data confidence")
                    c = None
            if c and c.alpha_score >= min_alpha:
                results.append(c)
            time.sleep(0.25)
        results.sort(key=lambda c: c.alpha_score, reverse=True)
        return results
    
    def _check_smart_exits(self, portfolio, regime):
        """
        Ask AI Council: HOLD or SELL each position?
        LLM decides based on trend, momentum, P&L — no hardcoded thresholds.
        Falls back to trailing stop if LLM unavailable.
        """
        if not portfolio.positions:
            return

        exit_decisions = self.council.vote_exits(portfolio.positions, regime)

        for ticker, decision in exit_decisions.items():
            action = decision["action"]
            reason = decision["reasoning"]
            conf   = decision["confidence"]

            if action == "SELL":
                pos = portfolio.positions.get(ticker)
                if not pos:
                    continue
                pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
                try:
                    view = self.executor._trend_view(ticker, pos)
                except Exception:
                    view = {"healthy": False, "weak": True, "trend": "unknown", "rsi": 50, "perf_w": 0}
                first_book_pct = float(CONFIG.get("profit_book_first_pct", CONFIG.get("variable_profit_min_pct", 0.04))) * 100
                runner_pct = float(CONFIG.get("profit_book_runner_pct", CONFIG.get("variable_profit_max_pct", 0.08))) * 100
                strong_runner = (
                    pnl_pct >= runner_pct
                    and bool(view.get("healthy"))
                    and str(view.get("trend")) == "uptrend"
                    and not bool(view.get("weak"))
                    and float(view.get("perf_w", 0)) >= float(CONFIG.get("runner_min_weekly_perf_pct", 0.0))
                )
                if strong_runner:
                    log.info(
                        f"   HOLD RUNNER {ticker}: AI wanted sell ({reason}), "
                        f"but chart is strong for bigger gains | P&L {pnl_pct:+.2f}%"
                    )
                    continue
                if pnl_pct >= first_book_pct and not getattr(pos, "t1_hit", False):
                    sell_qty = pos.qty if pos.qty <= 1 else max(
                        1,
                        int(pos.qty * float(CONFIG.get("profit_book_first_sell_fraction", 0.50))),
                    )
                    log.warning(
                        f"   PROFIT BOOK {ticker}: {reason} | P&L {pnl_pct:+.2f}% | "
                        f"selling {sell_qty}/{pos.qty}"
                    )
                    if self.executor.sell(ticker, sell_qty, f"AI-Exit partial profit: {reason}"):
                        if hasattr(self.executor, "_trade_meta"):
                            meta = self.executor._trade_meta.get(ticker.upper(), {})
                            if meta:
                                meta["t1_hit"] = True
                                self.executor._save_trade_meta()
                        if ticker in getattr(self.executor, "_portfolio", portfolio).positions:
                            getattr(self.executor, "_portfolio", portfolio).positions[ticker].t1_hit = True
                            if hasattr(self.executor, "_save_state"):
                                self.executor._save_state()
                    continue
                log.warning(f"   EXIT {ticker}: {reason} | P&L {pnl_pct:+.2f}% | conf {conf:.0%}")
                if self.executor.sell(ticker, pos.qty, f"AI-Exit: {reason}"):
                    log.info(f"   ✅ SOLD {ticker}")
                    self.exit_manager.learn_from_trade({
                        "ticker":      ticker,
                        "tier":        pos.tier if hasattr(pos, 'tier') else 3,
                        "entry_price": pos.avg_cost,
                        "exit_price":  pos.current_price,
                        "exit_reason": reason,
                        "pnl_pct":     pnl_pct,
                        "exit_type":   "ai_sell",
                        "days_held":   self._calculate_days_held(pos.buy_date if hasattr(pos, 'buy_date') else None),
                        "rsi_at_exit": 50
                    })
            else:
                pos = portfolio.positions.get(ticker)
                if pos:
                    pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
                    log.info(f"   HOLD {ticker}: {reason} | P&L {pnl_pct:+.2f}%")
    
    def _calculate_days_held(self, buy_date):
        """Calculate number of days a position has been held"""
        if not buy_date:
            return 0
        try:
            from datetime import datetime
            entry_dt = datetime.fromisoformat(buy_date) if isinstance(buy_date, str) else buy_date
            return max(0, (datetime.now() - entry_dt.replace(tzinfo=None)).days)
        except:
            return 0

    def _check_stale_exits(self, portfolio):
        """Exit positions that haven't moved after too many days, freeing slots for fresh signals."""
        return

def check():
    allowed_modes = {"broker", "signals_only"}
    if CONFIG["execution_mode"] not in allowed_modes:
        print(f"\nInvalid EXECUTION_MODE='{CONFIG['execution_mode']}'. Use one of: broker, signals_only")
        sys.exit(1)

    req = {}
    if CONFIG.get("council_mode") == "openrouter":
        req["OPENROUTER_API_KEY"] = CONFIG["openrouter_key"]
    if CONFIG["execution_mode"] == "broker":
        req["DHAN_CLIENT_ID"] = CONFIG["dhan_client_id"]
        req["DHAN_ACCESS_TOKEN"] = CONFIG["dhan_access_token"]
    missing = [k for k, v in req.items() if str(v).startswith("YOUR")]
    if missing:
        print("\nMissing API keys:")
        for m in missing: print(f"  set {m}=your_key_here")
        sys.exit(1)

LIVE_BUDGET_STATE_FILE = project_path("live_budget_state.json")

def _load_live_budget_state() -> dict:
    try:
        if os.path.exists(LIVE_BUDGET_STATE_FILE):
            with open(LIVE_BUDGET_STATE_FILE, "r", encoding="utf-8-sig") as f:
                return json.load(f)
    except Exception:
        pass
    return {"cap": 0.0}

def _save_live_budget_state(cap: float):
    try:
        with open(LIVE_BUDGET_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"cap": round(cap, 2)}, f)
    except Exception:
        pass

def _get_dhan_balance() -> float:
    try:
        from dotenv import load_dotenv
        from dhanhq import dhanhq

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        load_dotenv(os.path.join(project_root, ".env"), override=True)

        client_id = os.getenv("DHAN_CLIENT_ID", CONFIG.get("dhan_client_id", ""))
        access_token = os.getenv("DHAN_ACCESS_TOKEN", CONFIG.get("dhan_access_token", ""))

        if not client_id or not access_token or client_id.startswith("YOUR") or access_token.startswith("YOUR"):
            print("  Warning: Dhan credentials missing or default in .env")
            return 0.0

        client = dhanhq(client_id=client_id, access_token=access_token)
        resp = client.get_fund_limits()
        funds = resp.get("data", resp) if isinstance(resp, dict) else {}
        balance = float(
            funds.get("availabelBalance")
            or funds.get("availableBalance")
            or funds.get("cashBalance")
            or funds.get("withdrawableBalance")
            or 0.0
        )
        return balance
    except Exception as e:
        print(f"  Warning: could not fetch Dhan balance ({e})")
        return 0.0

def _saved_position_count() -> int:
    try:
        if not os.path.exists(PORTFOLIO_STATE_FILE):
            return 0
        with open(PORTFOLIO_STATE_FILE, "r", encoding="utf-8-sig") as f:
            state = json.load(f)
        return len(state.get("positions", {}) or {})
    except Exception:
        return 0

def _saved_portfolio_total() -> float:
    try:
        if not os.path.exists(PORTFOLIO_STATE_FILE):
            return 0.0
        with open(PORTFOLIO_STATE_FILE, "r", encoding="utf-8-sig") as f:
            state = json.load(f)
        cash = float(state.get("cash_usd") or 0.0)
        invested = sum(
            float(p.get("market_value") or 0.0)
            for p in (state.get("positions") or {}).values()
        )
        return cash + invested
    except Exception:
        return 0.0

def startup_prompt() -> Tuple[str, float]:
    is_live = (CONFIG["execution_mode"] == "broker" and not CONFIG["broker_paper"])
    run_mode = "discover"

    if not sys.stdin.isatty():
        if is_live:
            live_state = _load_live_budget_state()
            prev_cap = float(live_state.get("cap") or 0.0)
            if prev_cap > 0:
                return run_mode, prev_cap
            dhan_bal = _get_dhan_balance()
            if dhan_bal > 0:
                _save_live_budget_state(dhan_bal)
                return run_mode, dhan_bal
        prev_total = _saved_portfolio_total()
        if prev_total > 0:
            return run_mode, prev_total
        return run_mode, float(CONFIG.get("trading_budget_default", CONFIG.get("trading_budget_max", 50000)))

    saved_positions = _saved_position_count()
    print("\nMax Alpha startup")
    print(f"Saved bot positions found: {saved_positions}")
    print("Combined mode: manage existing holdings first, then scan for new buys.")

    if is_live:
        print("\n  LIVE TRADING MODE — real money")
        print("  Fetching your Dhan wallet balance...")
        dhan_balance = _get_dhan_balance()
        live_state = _load_live_budget_state()
        prev_cap = float(live_state.get("cap") or 0.0)

        if dhan_balance > 0:
            print(f"  Current Dhan wallet balance: Rs {dhan_balance:,.2f}")
        else:
            print("  Could not fetch Dhan balance — check credentials and token.")

        if prev_cap > 0:
            print(f"  Previously allocated to bot:  Rs {prev_cap:,.2f}")
            prompt = f"  How much to ADD to bot allocation? (empty = keep Rs {prev_cap:,.2f}): "
        else:
            prompt = (
                f"  How much to allocate to bot? (empty = use full Rs {dhan_balance:,.2f}): "
                if dhan_balance > 0 else
                "  How much to allocate to bot (in rupees): "
            )

        raw = input(prompt).strip()

        if not raw:
            cap = prev_cap if prev_cap > 0 else dhan_balance
        else:
            try:
                added = float(raw.replace(",", ""))
            except ValueError:
                print("  Invalid amount; keeping previous allocation.")
                added = 0.0
            cap = prev_cap + added

        if dhan_balance > 0 and cap > dhan_balance:
            print(
                f"  Not enough wallet balance. "
                f"Maximum wallet balance is Rs {dhan_balance:,.2f}. "
                f"Starting with Rs {dhan_balance:,.2f}."
            )
            cap = dhan_balance

        if cap <= 0:
            cap = dhan_balance if dhan_balance > 0 else float(CONFIG.get("trading_budget_default", 50000))

        _save_live_budget_state(cap)
        print(f"  Bot allocated: Rs {cap:,.2f} from your Dhan wallet.")
        return run_mode, cap

    min_budget = float(CONFIG.get("trading_budget_min", 1000))
    max_budget = float(CONFIG.get("trading_budget_max", 50000))

    prev_total = _saved_portfolio_total()
    if prev_total > 0:
        default_budget = prev_total
        default_label = f"Rs {prev_total:,.2f} (previous portfolio balance)"
    else:
        default_budget = float(CONFIG.get("trading_budget_default", max_budget))
        default_label = f"Rs {default_budget:,.0f}"

    raw = input(
        f"Total trading wallet cap in rupees [default {default_label}; "
        f"allowed Rs {min_budget:,.0f}-Rs {max_budget:,.0f}]: "
    ).strip()

    if raw:
        try:
            budget = float(raw.replace(",", ""))
        except ValueError:
            print("Invalid amount; using default.")
            budget = default_budget
    else:
        budget = default_budget

    budget = max(min_budget, min(max_budget, budget))
    print(f"Wallet cap set to Rs {budget:,.2f}.")
    return run_mode, budget
