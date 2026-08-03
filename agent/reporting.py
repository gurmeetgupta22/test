import datetime
import json
import os
from typing import List

try:
    from .models import MarketRegime, Portfolio, StockSignal
    from .settings import CONFIG, IST, PERFORMANCE_FILE, log
except ImportError:
    if __package__:
        raise
    from models import MarketRegime, Portfolio, StockSignal
    from settings import CONFIG, IST, PERFORMANCE_FILE, log

class Tracker:

    def __init__(self):
        self.file_path = PERFORMANCE_FILE
        self.history: List[dict] = []
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.history = data
        except Exception as e:
            log.warning(f"Could not load dashboard history: {e}")
        self.cycle = int(self.history[-1].get("cycle", 0)) if self.history else 0

    def record(self, portfolio: Portfolio, regime: MarketRegime, signals: int, executor=None):
        self.cycle += 1
        analyses = executor.analyze_positions(portfolio) if executor and hasattr(executor, "analyze_positions") else []
        stats = executor.trade_stats() if executor and hasattr(executor, "trade_stats") else {}
        risk_status = executor.risk.status(portfolio) if executor and hasattr(executor, "risk") else {}
        wallet_cap = getattr(executor, "_initial_budget", portfolio.total_usd)
        entry = {
            "cycle": self.cycle,
            "ts":    datetime.datetime.now(IST).isoformat(),
            "total_usd":  round(portfolio.total_usd, 2),
            "cash_usd":   round(portfolio.cash_usd, 2),
            "wallet_cap":  round(wallet_cap, 2),
            "invested_usd": round(portfolio.invested_usd, 2),
            "positions":  portfolio.open_count,
            "tier1_usd":  round(portfolio.tier1_value, 2),
            "tier2_usd":  round(portfolio.tier2_value, 2),
            "tier3_usd":  round(portfolio.tier3_value, 2),
            "regime":     regime.state,
            "regime_score": regime.score,
            "signals":    signals,
            "positions_detail": analyses,
            "trade_stats": stats,
            "risk_status": risk_status,
        }
        self.history.append(entry)
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except Exception: pass

    def print_dashboard(self, portfolio: Portfolio, regime: MarketRegime):
        t = portfolio.total_usd
        log.info(f"\n{'='*72}")
        log.info(f"  CYCLE #{self.cycle} | {datetime.datetime.now(IST).strftime('%a %d %b %H:%M IST')}")
        log.info(f"{'='*72}")
        log.info(f"  Portfolio : Rs{t:>12,.2f} INR")
        log.info(f"  Cash      : Rs{portfolio.cash_usd:>12,.2f} INR   (deployable: Rs{portfolio.deployable_cash:>,.2f})")
        log.info(f"  Positions : {portfolio.open_count}/{CONFIG['max_open_positions']}")
        log.info(f"  Regime    : {regime.state} (score:{regime.score:.2f}) | {regime.reasoning[:60]}")
        log.info(f"  Allocation: T1:{regime.allocation['tier1']:.0%} T2:{regime.allocation['tier2']:.0%} T3:{regime.allocation['tier3']:.0%}")
        log.info(f"  Deployed  : T1:Rs{portfolio.tier1_value:,.0f} T2:Rs{portfolio.tier2_value:,.0f} T3:Rs{portfolio.tier3_value:,.0f}")
        if portfolio.positions:
            log.info(f"\n  {'Tkr':<7} {'T':>2} {'Qty':>5} {'Cost':>9} {'Now':>9} {'P&L':>8} {'Peak':>9}")
            log.info(f"  {'-'*56}")
            for tkr, pos in portfolio.positions.items():
                pnl = (pos.current_price-pos.avg_cost)/pos.avg_cost*100
                log.info(f"  {tkr:<7} T{pos.tier} {pos.qty:>5} "
                         f"Rs{pos.avg_cost:>8.4f} Rs{pos.current_price:>8.4f} "
                         f"{'+'if pnl>=0 else ''}{pnl:>6.1f}% Rs{pos.peak_price:>8.4f}")
        log.info(f"{'='*72}\n")

class SignalExporter:

    def __init__(self, file_path: str):
        self.file_path = file_path

    def export(self, signals: List[StockSignal], regime: MarketRegime,
               market_open: bool, cycle: int):
        payload = {
            "ts": datetime.datetime.now(IST).isoformat(),
            "cycle": cycle,
            "mode": "signals_only",
            "market_open": market_open,
            "regime": {
                "state": regime.state,
                "score": regime.score,
                "reasoning": regime.reasoning,
            },
            "signals": [
                {
                    "action": s.action,
                    "ticker": s.ticker,
                    "tier": s.tier,
                    "qty": s.qty,
                    "entry_price_inr": s.price,
                    "trade_value_inr": s.value_usd,
                    "stop_loss_inr": s.stop_loss,
                    "take_profit_inr": s.take_profit,
                    "target_1_inr": s.target_1,
                    "trailing_stop_inr": s.trail_stop,
                    "hard_exit_time": s.hard_exit_time,
                    "consensus": round(s.consensus, 4),
                    "strategy": s.strategy,
                    "reasoning": s.reasoning,
                    "fundamental_quality": s.fundamental_quality,
                    "data_confidence": s.data_confidence,
                }
                for s in signals
            ],
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=True)
            log.info(f"  Signals exported for paper execution: {self.file_path}")
        except Exception as e:
            log.error(f"  Failed to export paper signals: {e}")
