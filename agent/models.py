from dataclasses import dataclass, field
from typing import Dict, List

try:
    from .settings import CONFIG
except ImportError:
    if __package__:
        raise
    from settings import CONFIG

@dataclass
class MarketRegime:
    state: str          # BULL / NEUTRAL / BEAR / CRASH
    score: float        # 0â€“1 composite score
    spy_trend: str      # uptrend / downtrend / sideways
    spy_rsi: float
    vix_level: str      # low / elevated / high / extreme
    breadth: float      # % stocks above 50-day MA (0â€“1)
    momentum: float     # NIFTY 20-day return
    allocation: Dict[str, float]
    reasoning: str

@dataclass
class StockSignal:
    ticker: str
    tier: int           # 1, 2, or 3
    action: str         # BUY / SELL / HOLD
    consensus: float
    price: float
    qty: int
    value_usd: float
    stop_loss: float
    take_profit: float
    trail_stop: float
    strategy: str
    reasoning: str
    alpha_score: float
    fundamental_quality: str = "DATA_UNAVAILABLE"
    data_confidence: str = "VERY_LOW"
    target_1: float = 0.0
    hard_exit_time: str = ""

@dataclass
class Position:
    ticker: str
    tier: int
    qty: int
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    peak_price: float
    buy_date: str = ""
    strategy: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    target_1: float = 0.0
    hard_exit_time: str = ""
    t1_hit: bool = False

@dataclass
class FundamentalView:
    valuation: str
    growth: str
    health: str
    returns: str
    ownership: str
    overall_quality: str
    data_confidence: str
    score: float
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

@dataclass
class Portfolio:
    total_usd: float
    cash_usd: float
    invested_usd: float
    positions: Dict[str, Position]
    open_count: int
    # Tier breakdowns
    tier1_value: float = 0.0
    tier2_value: float = 0.0
    tier3_value: float = 0.0

    @property
    def deployable_cash(self) -> float:
        """Cash available to deploy  keeps 5% in reserve."""
        reserve = self.total_usd * CONFIG["min_cash_reserve"]
        return max(0, self.cash_usd - reserve)

    def tier_capacity(self, tier: int, regime: MarketRegime) -> float:
        """How much more USD can go into this tier given regime allocations."""
        alloc = regime.allocation.get(f"tier{tier}", 0)
        target = self.total_usd * alloc
        current = getattr(self, f"tier{tier}_value", 0)
        return max(0, target - current)

@dataclass
class ScoredStock:
    ticker: str
    tier: int
    price: float
    alpha_score: float
    rsi: float
    macd_signal: str
    trend: str
    rel_volume: float
    volume: int
    change_1d: float
    change_5d: float
    gap_pct: float
    squeeze_score: float
    bb_squeeze: bool
    near_52w_high: bool
    has_news: bool
    news_headlines: List[str]
    short_pct: float
    market_cap: float
    sector: str
    pe_ratio: float
    dividend_yield: float
    price_to_book: float
    debt_to_equity: float
    current_ratio: float
    roe: float
    revenue_growth: float
    earnings_growth: float
    fundamental: FundamentalView
    strategy_setup: str = ""
    strategy_direction: str = ""
    strategy_stop_loss: float = 0.0
    strategy_target_1: float = 0.0
    strategy_take_profit: float = 0.0
    strategy_hard_exit_time: str = ""
    strategy_notes: str = ""
    history_pattern: str = "unknown"
    recovery_score: float = 0.0
    downtrend_risk: float = 1.0
