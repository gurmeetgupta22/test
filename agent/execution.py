import datetime
import json
import math
import os
from typing import Dict, List, Optional

import numpy as np
from dhanhq import dhanhq

try:
    from .market_data import plain_ticker, tradingview_scan
    from .models import Portfolio, Position, StockSignal
    from .risk import AlertManager, LearningEngine, RiskManager, TradeLedger
    from .settings import CONFIG, IST, PORTFOLIO_STATE_FILE, log, project_path
except ImportError:
    if __package__:
        raise
    from market_data import plain_ticker, tradingview_scan
    from models import Portfolio, Position, StockSignal
    from risk import AlertManager, LearningEngine, RiskManager, TradeLedger
    from settings import CONFIG, IST, PORTFOLIO_STATE_FILE, log, project_path


def _variable_profit_pct(trade_value: float, strength: float = 0.5) -> float:
    strength = max(0.0, min(1.0, strength))
    lo = float(CONFIG.get("variable_profit_min_pct", 0.05))
    hi = float(CONFIG.get("variable_profit_max_pct", 0.08))
    technical_target = lo if hi <= lo else lo + (hi - lo) * strength
    if trade_value <= 0:
        return technical_target
    min_profit = float(CONFIG.get("profit_target_min_rupees", 2000))
    max_profit = float(CONFIG.get("profit_target_max_rupees", 5000))
    rupee_target = min_profit + max(0.0, max_profit - min_profit) * strength
    rupee_target_pct = rupee_target / max(1.0, trade_value)
    cap = float(CONFIG.get("profit_target_cap_pct", 0.50))
    return max(float(CONFIG.get("profit_floor_pct", 0.01)), min(cap, min(max(technical_target, rupee_target_pct), hi)))


def _position_profit_pct(pos: Position, strength: float = 0.5) -> float:
    if pos.avg_cost and pos.take_profit and pos.take_profit > pos.avg_cost:
        stored = max(0.0, (pos.take_profit - pos.avg_cost) / pos.avg_cost)
        first_book = float(CONFIG.get("profit_book_first_pct", CONFIG.get("variable_profit_min_pct", 0.04)))
        return min(stored, first_book)
    return _variable_profit_pct(pos.qty * pos.avg_cost, strength)


def _is_strong_runner(pos: Position, view: Dict) -> bool:
    peak = float(pos.peak_price or pos.current_price or 0.0)
    current = float(pos.current_price or 0.0)
    near_peak = peak <= 0 or current >= peak * (1 - float(CONFIG.get("runner_max_peak_pullback_pct", 0.03)))
    rsi = float(view.get("rsi", 50.0))
    return (
        bool(view.get("healthy"))
        and str(view.get("trend")) == "uptrend"
        and not bool(view.get("weak"))
        and near_peak
        and 48 <= rsi <= float(CONFIG.get("runner_max_rsi", 76))
        and float(view.get("perf_w", 0.0)) >= float(CONFIG.get("runner_min_weekly_perf_pct", 0.0))
    )


def _chart_breakdown(pos: Position, view: Dict, pnl: float) -> bool:
    peak_drop = float(CONFIG.get("chart_breakdown_peak_drop_pct", 0.08))
    week_drop = float(CONFIG.get("chart_breakdown_weekly_drop_pct", -5.0))
    month_drop = float(CONFIG.get("chart_breakdown_monthly_drop_pct", -10.0))
    peak = float(pos.peak_price or pos.current_price or 0.0)
    peak_failed = peak > 0 and float(pos.current_price or 0.0) <= peak * (1 - peak_drop)
    trend = str(view.get("trend", "unknown"))
    rsi = float(view.get("rsi", 50.0))
    perf_w = float(view.get("perf_w", 0.0))
    perf_m = float(view.get("perf_m", 0.0))
    return (
        pnl < 0
        and (
            trend == "downtrend"
            or (trend == "weakening" and rsi < 38 and perf_w <= week_drop)
            or (perf_w <= week_drop and perf_m <= month_drop)
            or peak_failed
        )
    )


class Executor:

    TIER_MAP_FILE = project_path("tier_map.json")       # Persists which tier each position belongs to
    SYMBOL_MAP_FILE = project_path("symbol_map.json")   # Maps ticker -> Dhan security_id
    TRADE_META_FILE = project_path("trade_meta.json")   # Persists strategy-specific stops/targets

    def __init__(self, budget_cap: float = 0.0):
        self.client = dhanhq(
            client_id=CONFIG["dhan_client_id"],
            access_token=CONFIG["dhan_access_token"],
        )
        self._initial_budget = budget_cap
        self._peak: Dict[str, float] = {}
        self._loss_watch: Dict[str, dict] = {}
        self._tier_map: Dict[str, int] = self._load_tier_map()
        self._symbol_map: Dict[str, str] = self._load_symbol_map()
        self._trade_meta: Dict[str, dict] = self._load_trade_meta()
        self.risk = RiskManager()
        self.ledger = TradeLedger()
        self.learning = LearningEngine()
        self.alerts = AlertManager()
        mode = "PAPER-SAFE" if CONFIG["broker_paper"] else "LIVE ***"
        log.info(f"Dhan connected — {mode}")
        if self._initial_budget > 0:
            log.info(f"  Live trading budget capped at Rs {self._initial_budget:,.2f}")
        if not self._symbol_map:
            log.warning("symbol_map.json not found or empty — order placement needs security_id mappings")

    def _load_tier_map(self) -> Dict[str, int]:
        try:
            with open(self.TIER_MAP_FILE, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_tier_map(self):
        with open(self.TIER_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(self._tier_map, f)

    def _load_symbol_map(self) -> Dict[str, str]:
        try:
            with open(self.SYMBOL_MAP_FILE, encoding="utf-8-sig") as f:
                data = json.load(f)
                return {str(k).upper(): str(v) for k, v in data.items()}
        except Exception:
            return {}

    def _load_trade_meta(self) -> Dict[str, dict]:
        try:
            with open(self.TRADE_META_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return {str(k).upper(): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            return {}

    def _save_trade_meta(self):
        with open(self.TRADE_META_FILE, "w", encoding="utf-8") as f:
            json.dump(self._trade_meta, f, indent=2)

    def _security_id(self, ticker: str) -> Optional[str]:
        return self._symbol_map.get(ticker.upper())

    @staticmethod
    def _num(value, default=0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _holding_days(pos: Position) -> int:
        try:
            return max(0, (datetime.date.today() - datetime.date.fromisoformat(pos.buy_date[:10])).days)
        except Exception:
            return 0

    @staticmethod
    def _stored_take_profit_pct(pos: Position) -> float:
        if pos.avg_cost and pos.take_profit:
            stored = max(0.0, (pos.take_profit - pos.avg_cost) / pos.avg_cost)
            first_book = float(CONFIG.get("profit_book_first_pct", CONFIG.get("variable_profit_min_pct", 0.04)))
            return min(stored, first_book)
        return _position_profit_pct(pos)

    @staticmethod
    def _trend_view(ticker: str, pos: Position) -> Dict:
        view = {
            "trend": "unknown",
            "healthy": False,
            "weak": True,
            "rsi": 50.0,
            "perf_w": 0.0,
            "perf_m": 0.0,
        }
        try:
            rows = tradingview_scan(
                [ticker],
                ["close", "RSI", "SMA20", "SMA50", "Perf.W", "Perf.1M"],
            )
            row = rows.get(plain_ticker(ticker), {})
            current = float(row.get("close") or pos.current_price)
            rsi = float(row.get("RSI") or 50.0)
            sma20 = float(row.get("SMA20") or current)
            sma50 = float(row.get("SMA50") or current)
            perf_w = float(row.get("Perf.W") or 0.0)
            perf_m = float(row.get("Perf.1M") or 0.0)
            healthy = current >= sma20 >= sma50 or (current >= sma20 and perf_w > 0 and perf_m > -2)
            downtrend = current < sma20 < sma50 or (perf_w <= float(CONFIG.get("chart_breakdown_weekly_drop_pct", -5.0)) and rsi < 40)
            weak = downtrend or current < sma20 or rsi < 38 or perf_w < -3
            trend = "uptrend" if healthy else "downtrend" if downtrend else "weakening" if weak else "mixed"
            view.update({
                "trend": trend,
                "healthy": healthy,
                "weak": weak,
                "rsi": rsi,
                "perf_w": perf_w,
                "perf_m": perf_m,
            })
        except Exception as e:
            log.debug(f"Trend view failed for {ticker}: {e}")
        return view

    def get_portfolio(self) -> Portfolio:
        funds_resp = self.client.get_fund_limits()
        pos_resp = self.client.get_positions()

        funds = funds_resp.get("data", funds_resp) if isinstance(funds_resp, dict) else {}
        raw = pos_resp.get("data", pos_resp) if isinstance(pos_resp, dict) else []
        if not isinstance(raw, list):
            raw = []

        cash_usd = self._num(
            funds.get("availabelBalance")
            or funds.get("availableBalance")
            or funds.get("cashBalance")
            or funds.get("withdrawableBalance")
            or 0.0
        )

        positions = {}
        t1v = t2v = t3v = 0.0

        for p in raw:
            t = str(p.get("tradingSymbol") or p.get("securityId") or "").upper()
            if not t:
                continue
            cur = self._num(p.get("ltp") or p.get("lastTradedPrice") or p.get("costPrice") or 0.0)
            self._peak[t] = max(self._peak.get(t, cur), cur)
            tier = self._tier_map.get(t, 2)
            meta = self._trade_meta.get(t, {})
            qty = int(abs(self._num(p.get("netQty") or p.get("quantity") or 0)))
            avg_cost = self._num(p.get("costPrice") or p.get("buyAvg") or p.get("avgPrice") or cur)
            mv = abs(self._num(p.get("positionValue") or p.get("marketValue") or (qty * cur)))
            pnl = self._num(p.get("unrealizedProfit") or p.get("unRealizedProfit") or (mv - (qty * avg_cost)))
            pos  = Position(
                ticker=t, tier=tier, qty=qty,
                avg_cost=avg_cost,
                current_price=cur, market_value=mv,
                unrealized_pnl=pnl,
                peak_price=self._peak[t],
                strategy=meta.get("strategy", ""),
                stop_loss=float(meta.get("stop_loss") or 0.0),
                take_profit=float(meta.get("take_profit") or 0.0),
                target_1=float(meta.get("target_1") or 0.0),
                hard_exit_time=meta.get("hard_exit_time", ""),
                t1_hit=bool(meta.get("t1_hit", False)),
            )
            if pos.strategy != "orb_long_breakout":
                pos.stop_loss = 0.0
                pos.take_profit = round(pos.avg_cost * (1 + _position_profit_pct(pos)), 4)
            positions[t] = pos
            if tier == 1: t1v += mv
            elif tier == 2: t2v += mv
            else: t3v += mv

        invested = t1v + t2v + t3v
        if hasattr(self, "_initial_budget") and self._initial_budget > 0:
            allowed_cash = max(0.0, self._initial_budget - invested)
            cash_usd = min(cash_usd, allowed_cash)
        total_usd = cash_usd + invested

        return Portfolio(
            total_usd=total_usd, cash_usd=cash_usd,
            invested_usd=invested,
            positions=positions, open_count=len(raw),
            tier1_value=t1v, tier2_value=t2v, tier3_value=t3v,
        )

    def market_open(self) -> bool:
        now_ist = datetime.datetime.now(IST)
        if now_ist.weekday() >= 5:
            return False
        mins = now_ist.hour * 60 + now_ist.minute
        open_mins = 9 * 60 + 15
        close_mins = 15 * 60 + 30
        return open_mins <= mins < close_mins

    def _place_market_order(self, security_id: str, qty: int, side: str):
        side_const = self.client.BUY if side == "BUY" else self.client.SELL
        return self.client.place_order(
            security_id=str(security_id),
            exchange_segment=self.client.NSE_EQ,
            transaction_type=side_const,
            quantity=int(qty),
            order_type=self.client.MARKET,
            product_type=self.client.INTRA,
            price=0,
            trigger_price=0,
            disclosed_quantity=0,
            validity=self.client.DAY,
            after_market_order=False,
            amo_time="OPEN",
        )

    def _place_exit_order(self, security_id: str, qty: int, side: str, trigger_price: float, limit_price: float = 0.0):
        try:
            side_const = self.client.SELL if side == "SELL" else self.client.BUY
            order_type = getattr(self.client, "SLM", None) or getattr(self.client, "SL_M", None) or getattr(self.client, "STOP_LOSS_MARKET", None)
            if not order_type:
                order_type = self.client.MARKET
            return self.client.place_order(
                security_id=str(security_id),
                exchange_segment=self.client.NSE_EQ,
                transaction_type=side_const,
                quantity=int(qty),
                order_type=order_type,
                product_type=self.client.INTRA,
                price=float(limit_price or 0),
                trigger_price=float(trigger_price or 0),
                disclosed_quantity=0,
                validity=self.client.DAY,
                after_market_order=False,
                amo_time="OPEN",
            )
        except Exception as e:
            log.warning(f"  Protective order placement failed for {security_id}: {e}")
            return None

    def _place_target_order(self, security_id: str, qty: int, side: str, price: float):
        try:
            side_const = self.client.SELL if side == "SELL" else self.client.BUY
            order_type = getattr(self.client, "LIMIT", None) or self.client.MARKET
            return self.client.place_order(
                security_id=str(security_id),
                exchange_segment=self.client.NSE_EQ,
                transaction_type=side_const,
                quantity=int(qty),
                order_type=order_type,
                product_type=self.client.INTRA,
                price=float(price),
                trigger_price=0,
                disclosed_quantity=0,
                validity=self.client.DAY,
                after_market_order=False,
                amo_time="OPEN",
            )
        except Exception as e:
            log.warning(f"  Target order placement failed for {security_id}: {e}")
            return None

    def buy(self, signal: StockSignal) -> bool:
        try:
            portfolio = self.get_portfolio()
            if not self.risk.can_open(signal, portfolio):
                return False
            if CONFIG["broker_paper"]:
                log.info(f"  PAPER BUY skipped {signal.ticker} ({signal.qty})")
                self.risk.record_entry(signal)
                self.ledger.record("ENTRY", signal.ticker, "BUY", signal.qty, signal.price, signal.value_usd, strategy=signal.strategy)
                self.alerts.send("Paper BUY", f"{signal.qty}x {signal.ticker} @ Rs{signal.price:.2f} | {signal.strategy}")
                return True

            security_id = self._security_id(signal.ticker)
            if not security_id:
                log.error(f"  BUY failed {signal.ticker}: missing security_id mapping in {self.SYMBOL_MAP_FILE}")
                return False
            self._place_market_order(security_id, signal.qty, "BUY")
            self._tier_map[signal.ticker] = signal.tier
            self._save_tier_map()
            if signal.strategy:
                self._trade_meta[signal.ticker.upper()] = {
                    "strategy": signal.strategy,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "target_1": signal.target_1,
                    "hard_exit_time": signal.hard_exit_time,
                    "t1_hit": False,
                }
                self._save_trade_meta()
                if CONFIG["place_broker_target_orders"] and signal.take_profit:
                    self._place_target_order(security_id, signal.qty, "SELL", signal.take_profit)
            log.info(
                f"  BUY  T{signal.tier} {signal.qty:4d}x {signal.ticker:6s}"
                f" @ ~Rs{signal.price:.4f} (Rs{signal.value_usd:.0f})"
                f" | variable target:Rs{signal.take_profit:.4f}"
                f" | {signal.consensus:.0%} | {signal.strategy}"
            )
            self.risk.record_entry(signal)
            self.ledger.record("ENTRY", signal.ticker, "BUY", signal.qty, signal.price, signal.value_usd, strategy=signal.strategy)
            self.alerts.send("BUY", f"{signal.qty}x {signal.ticker} @ Rs{signal.price:.2f} | target Rs{signal.take_profit:.2f}")
            return True
        except Exception as e:
            log.error(f"  BUY failed {signal.ticker}: {e}")
            return False

    def sell(self, ticker: str, qty: int, reason: str) -> bool:
        try:
            existing_qty = 0
            try:
                existing = self.get_portfolio().positions.get(ticker)
                existing_qty = existing.qty if existing else 0
                sell_price = existing.current_price if existing else 0.0
                avg_cost = existing.avg_cost if existing else 0.0
            except Exception:
                existing_qty = 0
                sell_price = 0.0
                avg_cost = 0.0
            if CONFIG["broker_paper"]:
                log.info(f"  PAPER SELL skipped {ticker} ({qty}) | {reason}")
                pnl = (sell_price - avg_cost) * qty if avg_cost else 0.0
                self.risk.record_exit(pnl, reason)
                self.learning.record_outcome(ticker, existing.strategy if existing else "", pnl)
                self.ledger.record("EXIT", ticker, "SELL", qty, sell_price, qty * sell_price, pnl, reason)
                self.alerts.send("Paper SELL", f"{qty}x {ticker} @ Rs{sell_price:.2f} | PnL Rs{pnl:.2f} | {reason}")
                return True

            security_id = self._security_id(ticker)
            if not security_id:
                log.error(f"  SELL failed {ticker}: missing security_id mapping in {self.SYMBOL_MAP_FILE}")
                return False
            self._place_market_order(security_id, qty, "SELL")
            log.info(f"  SELL {qty:4d}x {ticker:6s} | {reason}")
            pnl = (sell_price - avg_cost) * qty if avg_cost else 0.0
            self.risk.record_exit(pnl, reason)
            self.learning.record_outcome(ticker, existing.strategy if existing else "", pnl)
            self.ledger.record("EXIT", ticker, "SELL", qty, sell_price, qty * sell_price, pnl, reason)
            self.alerts.send("SELL", f"{qty}x {ticker} @ Rs{sell_price:.2f} | PnL Rs{pnl:.2f} | {reason}")
            self._peak.pop(ticker, None)
            if not existing_qty or qty >= existing_qty:
                self._trade_meta.pop(ticker.upper(), None)
                self._save_trade_meta()
            return True
        except Exception as e:
            log.error(f"  SELL failed {ticker}: {e}")
            return False

    def _confirm_chart_exit(self, ticker: str, pos: Position, pnl: float, view: Dict) -> bool:
        if pnl >= 0 or not _chart_breakdown(pos, view, pnl):
            self._loss_watch.pop(ticker, None)
            return False
        current = float(pos.current_price or 0.0)
        watch = self._loss_watch.get(ticker, {})
        last_price = float(watch.get("last_price") or current)
        cycles = int(watch.get("cycles") or 0)
        if current >= last_price and view.get("trend") != "downtrend":
            self._loss_watch[ticker] = {"cycles": 0, "last_price": current, "trend": view.get("trend", "mixed")}
            log.info(f"  [RISK] Holding {ticker}: loss is stabilising; chart exit paused")
            return False
        cycles += 1
        needed = max(1, int(CONFIG.get("chart_breakdown_confirm_cycles", 2)))
        self._loss_watch[ticker] = {"cycles": cycles, "last_price": current, "trend": view.get("trend", "mixed")}
        if cycles < needed:
            log.info(
                f"  [RISK] Watching {ticker}: chart breakdown cycle {cycles}/{needed}; "
                f"trend {view.get('trend')}, RSI {float(view.get('rsi', 50)):.0f}, "
                f"{float(view.get('perf_w', 0)):+.1f}% 1w"
            )
            return False
        self._loss_watch.pop(ticker, None)
        return True

    def enforce_risk(self, portfolio: Portfolio):
        for ticker, pos in portfolio.positions.items():
            qty = pos.qty
            if pos.strategy == "orb_long_breakout" and pos.take_profit:
                if pos.target_1 and not pos.t1_hit and pos.current_price >= pos.target_1:
                    half = max(1, qty // 2)
                    if self.sell(ticker, half, "ORB T1 50% PROFIT"):
                        meta = self._trade_meta.get(ticker.upper(), {})
                        if meta:
                            meta["t1_hit"] = True
                            self._save_trade_meta()
                    continue
                if pos.current_price >= pos.take_profit:
                    self.sell(ticker, qty, "ORB T2 FULL EXIT")
                    continue

            avg   = pos.avg_cost
            cur   = pos.current_price
            peak  = pos.peak_price
            tier  = pos.tier
            pnl   = (cur-avg)/avg

            view = self._trend_view(ticker, pos)
            tp = self._stored_take_profit_pct(pos)
            runner_tp = float(CONFIG.get("profit_book_runner_pct", CONFIG.get("variable_profit_max_pct", 0.08)))

            if pnl < 0 and self._confirm_chart_exit(ticker, pos, pnl, view):
                self.sell(ticker, qty, f"CHART BREAKDOWN EXIT T{tier} {pnl:.1%}")
            elif pnl < 0:
                log.info(
                    f"  [RISK] Holding {ticker}: no fixed loss exit; "
                    f"chart {view['trend']}, RSI {view['rsi']:.0f}, {view['perf_w']:+.1f}% 1w"
                )
            elif pnl >= tp and not pos.t1_hit:
                sell_qty = qty if qty <= 1 else max(1, int(qty * float(CONFIG.get("profit_book_first_sell_fraction", 0.50))))
                if self.sell(ticker, sell_qty, f"FIRST PROFIT BOOK T{tier} {pnl:.1%}"):
                    meta = self._trade_meta.get(ticker.upper(), {})
                    if meta:
                        meta["t1_hit"] = True
                        self._save_trade_meta()
            elif pnl >= runner_tp and _is_strong_runner(pos, view):
                log.info(
                    f"  [RISK] Holding runner {ticker}: strong chart for bigger gains; "
                    f"P&L {pnl:.1%}, RSI {view['rsi']:.0f}, {view['perf_w']:+.1f}% 1w"
                )
            elif pnl >= runner_tp:
                sell_qty = max(1, int(qty * float(CONFIG.get("profit_book_runner_sell_fraction", 0.50))))
                self.sell(ticker, sell_qty, f"RUNNER PROFIT BOOK T{tier} {pnl:.1%}")
            elif pnl >= float(CONFIG.get("profit_floor_pct", 0.01)) and view["weak"]:
                self.sell(ticker, qty, f"PROFIT PROTECTION T{tier} {pnl:.1%} weakening chart")

class SimExecutor:
    """
    Persistent local simulation executor.
    Saves/loads portfolio state across bot restarts.
    Budget is user-specified at startup  never exceeded.
    """
    def __init__(self, budget: float = 1_000_000.0):
        self._budget         = budget
        self._state_file     = PORTFOLIO_STATE_FILE
        self._wins           = 0
        self._losses         = 0
        self._closed_trades: list = []
        self._order_history: list = []
        self._loss_watch: Dict[str, dict] = {}
        self._initial_budget = budget
        self.risk = RiskManager()
        self.ledger = TradeLedger()
        self.learning = LearningEngine()
        self.alerts = AlertManager()
        self._load_state()

    # â”€â”€ persistence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _load_state(self):
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, encoding="utf-8-sig") as f:
                    state = json.load(f)
                positions: Dict[str, Position] = {}
                for tkr, p in state.get("positions", {}).items():
                    positions[tkr] = Position(
                        ticker=p["ticker"], tier=p["tier"], qty=p["qty"],
                        avg_cost=p["avg_cost"], current_price=p["current_price"],
                        market_value=p["market_value"], unrealized_pnl=p["unrealized_pnl"],
                        peak_price=p["peak_price"],
                        buy_date=p.get("buy_date", ""),
                        strategy=p.get("strategy", ""),
                        stop_loss=float(p.get("stop_loss") or 0.0),
                        take_profit=float(p.get("take_profit") or 0.0),
                        target_1=float(p.get("target_1") or 0.0),
                        hard_exit_time=p.get("hard_exit_time", ""),
                        t1_hit=bool(p.get("t1_hit", False)),
                    )
                cash     = state.get("cash_usd", self._budget)
                invested = sum(p.market_value for p in positions.values())
                saved_initial = float(state.get("initial_budget") or 0.0)
                budget_cap = float(self._budget) if float(self._budget) > 0 else saved_initial

                # Budget 0 means "keep the remaining saved wallet"; positive values reset/add to the cap.
                if budget_cap > 0 and saved_initial > 0 and budget_cap != saved_initial:
                    diff = budget_cap - saved_initial
                    cash = max(0.0, cash + diff)
                elif budget_cap > 0 and cash + invested > budget_cap:
                    cash = max(0.0, budget_cap - invested)
                if CONFIG.get("reconcile_sim_cash", True) and budget_cap > 0:
                    realized = sum(float(t.get("pnl") or 0.0) for t in state.get("closed_trades", []))
                    open_cost = sum(p.qty * p.avg_cost for p in positions.values())
                    reconciled_cash = max(0.0, budget_cap + realized - open_cost)
                    if abs(reconciled_cash - float(cash or 0.0)) > 1.0:
                        log.warning(
                            f"Paper cash reconciled from saved trades: "
                            f"Rs{float(cash or 0.0):,.2f} -> Rs{reconciled_cash:,.2f}"
                        )
                        cash = reconciled_cash
                self._portfolio = Portfolio(
                    total_usd=cash + invested, cash_usd=cash, invested_usd=invested,
                    positions=positions, open_count=len(positions),
                    tier1_value=0.0, tier2_value=0.0, tier3_value=0.0,
                )
                self._wins           = state.get("wins", 0)
                self._losses         = state.get("losses", 0)
                self._closed_trades  = state.get("closed_trades", [])
                self._order_history  = state.get("order_history", [])
                self._loss_watch     = state.get("loss_watch", {}) if isinstance(state.get("loss_watch", {}), dict) else {}
                self._initial_budget = budget_cap
                log.info(f"Sim executor active  loaded {len(positions)} saved position(s) from previous session")
                return
            except Exception as e:
                log.warning(f"Could not load portfolio state ({e})  starting fresh")
        # Fresh start
        self._portfolio = Portfolio(
            total_usd=self._budget, cash_usd=self._budget, invested_usd=0.0,
            positions={}, open_count=0,
            tier1_value=0.0, tier2_value=0.0, tier3_value=0.0,
        )
        log.info(f"Sim executor active  paper portfolio initialized with Rs{self._budget:,.0f}")

    def _save_state(self):
        state = {
            "cash_usd":       round(self._portfolio.cash_usd, 4),
            "initial_budget": self._initial_budget,
            "wins":           self._wins,
            "losses":         self._losses,
            "closed_trades":  self._closed_trades[-50:],
            "order_history":   self._order_history[-200:],
            "loss_watch":      self._loss_watch,
            "positions": {
                tkr: {
                    "ticker": pos.ticker, "tier": pos.tier, "qty": pos.qty,
                    "avg_cost":       round(pos.avg_cost, 4),
                    "current_price":  round(pos.current_price, 4),
                    "market_value":   round(pos.market_value, 4),
                    "unrealized_pnl": round(pos.unrealized_pnl, 4),
                    "peak_price":     round(pos.peak_price, 4),
                    "buy_date":       pos.buy_date or datetime.date.today().isoformat(),
                    "strategy":       pos.strategy,
                    "stop_loss":      round(pos.stop_loss, 4),
                    "take_profit":    round(pos.take_profit, 4),
                    "target_1":       round(pos.target_1, 4),
                    "hard_exit_time": pos.hard_exit_time,
                    "t1_hit":         pos.t1_hit,
                }
                for tkr, pos in self._portfolio.positions.items()
            },
        }
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.warning(f"Could not save portfolio state: {e}")

    # â”€â”€ public interface â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def get_portfolio(self) -> Portfolio:
        if self._portfolio.positions:
            tickers = list(self._portfolio.positions.keys())
            updated = 0
            try:
                rows = tradingview_scan(tickers, ["close"])
                for t in tickers:
                    price = float(rows.get(plain_ticker(t), {}).get("close") or np.nan)
                    if not np.isnan(price):
                        self._portfolio.positions[t].current_price = price
                        self._portfolio.positions[t].peak_price = max(
                            self._portfolio.positions[t].peak_price, price)
                        updated += 1
            except Exception as e:
                log.debug(f"Sim price update failed: {e}")
            if updated < len(tickers):
                log.warning(
                    f"  [SIMULATOR] Price refresh updated {updated}/{len(tickers)} positions; "
                    "unchanged rows may show stale 0.0% P&L"
                )

        invested = 0.0
        t1 = t2 = t3 = 0.0
        for pos in self._portfolio.positions.values():
            if pos.strategy != "orb_long_breakout":
                pos.stop_loss = 0.0
                if not pos.take_profit:
                    pos.take_profit = round(pos.avg_cost * (1 + _position_profit_pct(pos)), 4)
            pos.market_value   = pos.qty * pos.current_price
            pos.unrealized_pnl = pos.market_value - (pos.qty * pos.avg_cost)
            invested += pos.market_value
            if pos.tier == 1:   t1 += pos.market_value
            elif pos.tier == 2: t2 += pos.market_value
            else:               t3 += pos.market_value
        self._portfolio.invested_usd = invested
        self._portfolio.total_usd    = self._portfolio.cash_usd + invested
        self._portfolio.tier1_value  = t1
        self._portfolio.tier2_value  = t2
        self._portfolio.tier3_value  = t3
        self._portfolio.open_count   = len(self._portfolio.positions)
        return self._portfolio

    def market_open(self) -> bool:
        now_ist = datetime.datetime.now(IST)
        if now_ist.weekday() >= 5:
            return False
        mins = now_ist.hour * 60 + now_ist.minute
        return (9 * 60 + 15) <= mins < (15 * 60 + 30)

    @staticmethod
    def _holding_days(pos: Position) -> int:
        try:
            return max(0, (datetime.date.today() - datetime.date.fromisoformat(pos.buy_date[:10])).days)
        except Exception:
            return 0

    @staticmethod
    def _stored_take_profit_pct(pos: Position) -> float:
        if pos.avg_cost and pos.take_profit:
            stored = max(0.0, (pos.take_profit - pos.avg_cost) / pos.avg_cost)
            first_book = float(CONFIG.get("profit_book_first_pct", CONFIG.get("variable_profit_min_pct", 0.04)))
            return min(stored, first_book)
        return _position_profit_pct(pos)

    @staticmethod
    def _trend_view(ticker: str, pos: Position) -> Dict:
        view = {
            "trend": "unknown",
            "healthy": False,
            "weak": True,
            "rsi": 50.0,
            "perf_w": 0.0,
            "perf_m": 0.0,
        }
        try:
            rows = tradingview_scan(
                [ticker],
                ["close", "RSI", "SMA20", "SMA50", "Perf.W", "Perf.1M"],
            )
            row = rows.get(plain_ticker(ticker), {})
            current = float(row.get("close") or pos.current_price)
            rsi = float(row.get("RSI") or 50.0)
            sma20 = float(row.get("SMA20") or current)
            sma50 = float(row.get("SMA50") or current)
            perf_w = float(row.get("Perf.W") or 0.0)
            perf_m = float(row.get("Perf.1M") or 0.0)
            healthy = current >= sma20 >= sma50 or (current >= sma20 and perf_w > 0 and perf_m > -2)
            downtrend = current < sma20 < sma50 or (perf_w <= float(CONFIG.get("chart_breakdown_weekly_drop_pct", -5.0)) and rsi < 40)
            weak = downtrend or current < sma20 or rsi < 38 or perf_w < -3
            trend = "uptrend" if healthy else "downtrend" if downtrend else "weakening" if weak else "mixed"
            view.update({
                "trend": trend,
                "healthy": healthy,
                "weak": weak,
                "rsi": rsi,
                "perf_w": perf_w,
                "perf_m": perf_m,
            })
        except Exception as e:
            log.debug(f"Trend view failed for {ticker}: {e}")
        return view

    def buy(self, signal: StockSignal) -> bool:
        if not self.risk.can_open(signal, self._portfolio):
            return False
        cost = signal.qty * signal.price
        if self._portfolio.cash_usd >= cost:
            self._portfolio.cash_usd -= cost
            if signal.ticker in self._portfolio.positions:
                p = self._portfolio.positions[signal.ticker]
                total_cost = (p.qty * p.avg_cost) + cost
                p.qty     += signal.qty
                p.avg_cost = total_cost / p.qty
                p.current_price = signal.price
                if signal.strategy:
                    p.strategy = signal.strategy
                    p.stop_loss = signal.stop_loss
                    p.take_profit = signal.take_profit
                    p.target_1 = signal.target_1
                    p.hard_exit_time = signal.hard_exit_time
            else:
                self._portfolio.positions[signal.ticker] = Position(
                    ticker=signal.ticker, tier=signal.tier, qty=signal.qty,
                    avg_cost=signal.price, current_price=signal.price,
                    market_value=cost, unrealized_pnl=0.0, peak_price=signal.price,
                    buy_date=datetime.date.today().isoformat(),
                    strategy=signal.strategy,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    target_1=signal.target_1,
                    hard_exit_time=signal.hard_exit_time,
                    t1_hit=False,
                )
            log.info(f"  [SIMULATOR] BOUGHT {signal.qty}x {signal.ticker} @ Rs{signal.price:.2f}")
            self._order_history.append({
                "event": "BUY",
                "ticker": signal.ticker,
                "qty": int(signal.qty),
                "price": round(signal.price, 4),
                "value": round(cost, 4),
                "strategy": signal.strategy,
                "reason": signal.reasoning,
                "ts": datetime.datetime.now(IST).isoformat(),
            })
            self.risk.record_entry(signal)
            self.ledger.record("ENTRY", signal.ticker, "BUY", signal.qty, signal.price, cost, strategy=signal.strategy)
            self.alerts.send("SIM BUY", f"{signal.qty}x {signal.ticker} @ Rs{signal.price:.2f} | {signal.strategy}")
            self._save_state()
            return True
        log.warning(f"  [SIMULATOR] Budget: need Rs{cost:.0f}, have Rs{self._portfolio.cash_usd:.0f}  skipping")
        return False

    def sell(self, ticker: str, qty: int, reason: str) -> bool:
        if ticker in self._portfolio.positions:
            pos        = self._portfolio.positions[ticker]
            sell_qty   = min(qty, pos.qty)
            proceeds   = sell_qty * pos.current_price
            cost_basis = sell_qty * pos.avg_cost
            pnl        = proceeds - cost_basis
            self._portfolio.cash_usd += proceeds
            pos.qty -= sell_qty
            log.info(f"  [SIMULATOR] SOLD {sell_qty}x {ticker} @ Rs{pos.current_price:.2f} | {reason} | PnL Rs{pnl:+,.2f}")
            self._order_history.append({
                "event": "SELL",
                "ticker": ticker,
                "qty": int(sell_qty),
                "price": round(pos.current_price, 4),
                "value": round(proceeds, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl / cost_basis * 100, 2) if cost_basis > 0 else 0,
                "strategy": pos.strategy,
                "reason": reason,
                "ts": datetime.datetime.now(IST).isoformat(),
            })
            self._closed_trades.append({
                "ticker": ticker, "qty": sell_qty,
                "buy_price": round(pos.avg_cost, 4),
                "sell_price": round(pos.current_price, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl / cost_basis * 100, 2) if cost_basis > 0 else 0,
                "reason": reason,
                "ts": datetime.datetime.now(IST).isoformat(),
            })
            if pnl > 0: self._wins   += 1
            else:        self._losses += 1
            self.risk.record_exit(pnl, reason)
            self.learning.record_outcome(ticker, pos.strategy, pnl)
            self.ledger.record("EXIT", ticker, "SELL", sell_qty, pos.current_price, proceeds, pnl, reason, pos.strategy)
            self.alerts.send("SIM SELL", f"{sell_qty}x {ticker} @ Rs{pos.current_price:.2f} | PnL Rs{pnl:+,.2f} | {reason}")
            if pos.qty <= 0:
                del self._portfolio.positions[ticker]
            self._save_state()
            return True
        return False

    def _confirm_chart_exit(self, ticker: str, pos: Position, pnl: float, view: Dict) -> bool:
        if pnl >= 0 or not _chart_breakdown(pos, view, pnl):
            if ticker in self._loss_watch:
                self._loss_watch.pop(ticker, None)
                self._save_state()
            return False
        current = float(pos.current_price or 0.0)
        watch = self._loss_watch.get(ticker, {})
        last_price = float(watch.get("last_price") or current)
        cycles = int(watch.get("cycles") or 0)
        if current >= last_price and view.get("trend") != "downtrend":
            self._loss_watch[ticker] = {"cycles": 0, "last_price": current, "trend": view.get("trend", "mixed")}
            self._save_state()
            log.info(f"  [RISK] Holding {ticker}: loss is stabilising; chart exit paused")
            return False
        cycles += 1
        needed = max(1, int(CONFIG.get("chart_breakdown_confirm_cycles", 2)))
        self._loss_watch[ticker] = {"cycles": cycles, "last_price": current, "trend": view.get("trend", "mixed")}
        self._save_state()
        if cycles < needed:
            log.info(
                f"  [RISK] Watching {ticker}: chart breakdown cycle {cycles}/{needed}; "
                f"trend {view.get('trend')}, RSI {float(view.get('rsi', 50)):.0f}, "
                f"{float(view.get('perf_w', 0)):+.1f}% 1w"
            )
            return False
        self._loss_watch.pop(ticker, None)
        self._save_state()
        return True

    def enforce_risk(self, portfolio: Portfolio):
        for ticker, pos in list(portfolio.positions.items()):
            if pos.strategy == "orb_long_breakout" and pos.take_profit:
                if pos.target_1 and not pos.t1_hit and pos.current_price >= pos.target_1:
                    half = max(1, pos.qty // 2)
                    if self.sell(ticker, half, "ORB T1 50% PROFIT"):
                        if ticker in self._portfolio.positions:
                            self._portfolio.positions[ticker].t1_hit = True
                            self._save_state()
                    continue
                if pos.current_price >= pos.take_profit:
                    self.sell(ticker, pos.qty, "ORB T2 FULL EXIT")
                    continue

            pnl = (pos.current_price - pos.avg_cost) / pos.avg_cost

            view = self._trend_view(ticker, pos)
            tp = self._stored_take_profit_pct(pos)
            runner_tp = float(CONFIG.get("profit_book_runner_pct", CONFIG.get("variable_profit_max_pct", 0.08)))
            if pnl < 0 and self._confirm_chart_exit(ticker, pos, pnl, view):
                self.sell(ticker, pos.qty, f"CHART BREAKDOWN EXIT {pnl:.1%}")
            elif pnl < 0:
                log.info(
                    f"  [RISK] Holding {ticker}: no fixed loss exit; "
                    f"chart {view['trend']}, RSI {view['rsi']:.0f}, {view['perf_w']:+.1f}% 1w"
                )
            elif pnl >= tp and not pos.t1_hit:
                sell_qty = pos.qty if pos.qty <= 1 else max(1, int(pos.qty * float(CONFIG.get("profit_book_first_sell_fraction", 0.50))))
                if self.sell(ticker, sell_qty, f"FIRST PROFIT BOOK {pnl:.1%}"):
                    if ticker in self._portfolio.positions:
                        self._portfolio.positions[ticker].t1_hit = True
                        self._save_state()
            elif pnl >= runner_tp and _is_strong_runner(pos, view):
                log.info(
                    f"  [RISK] Holding runner {ticker}: strong chart for bigger gains; "
                    f"P&L {pnl:.1%}, RSI {view['rsi']:.0f}, {view['perf_w']:+.1f}% 1w"
                )
            elif pnl >= runner_tp:
                sell_qty = max(1, int(pos.qty * float(CONFIG.get("profit_book_runner_sell_fraction", 0.50))))
                self.sell(ticker, sell_qty, f"RUNNER PROFIT BOOK {pnl:.1%}")
            elif pnl >= float(CONFIG.get("profit_floor_pct", 0.01)) and view["weak"]:
                self.sell(ticker, pos.qty, f"PROFIT PROTECTION {pnl:.1%} weakening chart")

    def sell_losers_only(self, portfolio: Portfolio):
        for ticker, pos in list(portfolio.positions.items()):
            pnl = (pos.current_price - pos.avg_cost) / pos.avg_cost
            view = self._trend_view(ticker, pos)
            if pnl < 0 and self._confirm_chart_exit(ticker, pos, pnl, view):
                self.sell(ticker, pos.qty, f"MANAGE: chart breakdown exit {pnl:.1%}")
            elif pnl > float(CONFIG.get("profit_floor_pct", 0.01)) and view["weak"]:
                self.sell(ticker, pos.qty, f"MANAGE: profit protection {pnl:.1%}")
            else:
                direction = "PROFIT" if pnl >= 0 else "HOLD - chart not broken"
                log.info(f"  [MANAGER] {ticker}: P&L {pnl:+.2%} - {direction}, council/health based hold")
        return

def _sim_trade_stats(self) -> dict:
    total = self._wins + self._losses
    return {
        "wins": self._wins,
        "losses": self._losses,
        "total": total,
        "win_rate": round((self._wins / total * 100), 2) if total else 0.0,
        "closed_trades": self._closed_trades[-50:],
        "order_history": self._order_history[-200:],
    }

def _sim_analyze_positions(self, portfolio: Optional[Portfolio] = None) -> List[dict]:
    portfolio = portfolio or self.get_portfolio()
    analyses = []
    rows = tradingview_scan(
        list(portfolio.positions.keys()),
        ["close", "Perf.W", "Perf.1M", "RSI", "SMA20", "SMA50", "Volatility.D"],
    )
    updated = 0
    for ticker, pos in portfolio.positions.items():
        momentum_20d = 0.0
        rsi = 50.0
        trend = "unknown"
        avg_abs_move = 1.0
        try:
            row = rows.get(plain_ticker(ticker), {})
            if row:
                current = float(row.get("close") or pos.current_price)
                pos.current_price = current
                pos.peak_price = max(pos.peak_price, current)
                updated += 1
                momentum_20d = float(row.get("Perf.1M") or 0.0)
                sma20 = float(row.get("SMA20") or current)
                sma50 = float(row.get("SMA50") or current)
                rsi = float(row.get("RSI") or 50.0)
                perf_w = float(row.get("Perf.W") or 0.0)
                downtrend = current < sma20 < sma50 or (
                    perf_w <= float(CONFIG.get("chart_breakdown_weekly_drop_pct", -5.0))
                    and rsi < 40
                )
                trend = "uptrend" if current >= sma20 >= sma50 else "downtrend" if downtrend else "weakening" if current < sma20 else "mixed"
                avg_abs_move = max(0.35, float(row.get("Volatility.D") or 1.0))
        except Exception as e:
            log.debug(f"Position analysis failed for {ticker}: {e}")

        pos.market_value = pos.qty * pos.current_price
        pos.unrealized_pnl = pos.market_value - (pos.qty * pos.avg_cost)
        pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost else 0.0
        target_pct = _position_profit_pct(pos, 0.7 if trend == "uptrend" else 0.35) * 100
        remaining_to_target = max(0.0, target_pct - pnl_pct)
        est_days = max(1, min(60, int(math.ceil(remaining_to_target / avg_abs_move)))) if remaining_to_target else 1
        est_sell_date = (datetime.date.today() + datetime.timedelta(days=est_days)).isoformat()
        win_prob = 50 + (momentum_20d * 1.2) + (8 if trend == "uptrend" else -12 if trend == "downtrend" else -8 if trend == "weakening" else 0)
        win_prob += 6 if pnl_pct > 0 else -10
        win_prob += 4 if 40 <= rsi <= 68 else -5 if rsi > 78 else 0
        win_prob = max(5, min(90, win_prob))
        expected_profit_pct = max(-50.0, min(target_pct * 1.2, pnl_pct + (momentum_20d * 0.35)))
        if pnl_pct >= target_pct:
            action = "SELL_PROFIT"
            reason = "variable profit target reached"
        elif pnl_pct > float(CONFIG.get("profit_floor_pct", 0.01)) * 100 and trend in ("weakening", "downtrend") and momentum_20d < 0:
            action = "SELL_PROFIT"
            reason = "profit exists but momentum is weakening"
        elif pnl_pct < 0 and trend == "downtrend":
            action = "SELL_BREAKDOWN"
            reason = "below buy price and chart is breaking down"
        elif pnl_pct < 0:
            action = "HOLD_RISK"
            reason = "below buy price; hold unless chart breaks down"
        else:
            action = "WAIT"
            reason = "healthy hold; variable profit target not reached yet"

        analyses.append({
            "ticker": ticker,
            "tier": pos.tier,
            "qty": pos.qty,
            "buy_price": round(pos.avg_cost, 4),
            "current_price": round(pos.current_price, 4),
            "market_value": round(pos.qty * pos.current_price, 2),
            "unrealized_pnl": round(pos.unrealized_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "peak_price": round(pos.peak_price, 4),
            "buy_date": pos.buy_date,
            "win_probability": round(win_prob, 1),
            "expected_profit_pct": round(expected_profit_pct, 2),
            "estimated_sell_date": est_sell_date,
            "action": action,
            "reason": reason,
        })
    if portfolio.positions and updated < len(portfolio.positions):
        log.warning(
            f"  [MANAGER] Price analysis refreshed {updated}/{len(portfolio.positions)} positions; "
            "some P&L/win-probability values may be stale"
        )
    return analyses

def _sim_manage_existing_positions(self, portfolio: Optional[Portfolio] = None):
    portfolio = portfolio or self.get_portfolio()
    for item in self.analyze_positions(portfolio):
        ticker = item["ticker"]
        pos = self._portfolio.positions.get(ticker)
        if not pos:
            continue
        log.info(
            f"  [MANAGER] {ticker}: {item['action']} | P&L {item['pnl_pct']:+.2f}% | "
            f"win probability {item['win_probability']:.1f}% | est sell {item['estimated_sell_date']} | {item['reason']}"
        )
        if item["action"] == "SELL_PROFIT" and item["pnl_pct"] > 0:
            self.sell(ticker, pos.qty, f"MANAGE: profit exit {item['pnl_pct']:+.2f}%")
        elif item["action"] == "SELL_BREAKDOWN":
            view = self._trend_view(ticker, pos)
            pnl = (pos.current_price - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0.0
            if self._confirm_chart_exit(ticker, pos, pnl, view):
                self.sell(ticker, pos.qty, f"MANAGE: chart breakdown exit {item['pnl_pct']:+.2f}%")
    self.get_portfolio()

SimExecutor.trade_stats = _sim_trade_stats
SimExecutor.analyze_positions = _sim_analyze_positions
SimExecutor.manage_existing_positions = _sim_manage_existing_positions
