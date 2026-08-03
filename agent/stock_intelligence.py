"""
Stock Intelligence: 3-Year Backtest Analysis for AI Exit Decisions
Fetches historical data, analyzes growth patterns, feeds into LLM council
"""

import logging
import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILE = Path("stock_intelligence_cache.json")


class StockIntelligence:
    """
    Fetches 3-year history for each held stock and produces a summary
    that the AI council uses to decide HOLD or SELL.

    Summary includes:
    - Long-term trend (3Y, 1Y, 6M, 3M)
    - Best/worst periods (when to hold, when to sell historically)
    - Seasonality (does it rise in June? Fall in Dec?)
    - Recovery pattern (how fast it bounces from dips)
    - Current position vs historical range
    """

    def __init__(self, cache_ttl_hours: int = 24):
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.cache: dict = self._load_cache()

    def _load_cache(self) -> dict:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            CACHE_FILE.write_text(json.dumps(self.cache, indent=2))
        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    def _cache_valid(self, ticker: str) -> bool:
        entry = self.cache.get(ticker, {})
        ts = entry.get("cached_at")
        if not ts:
            return False
        cached_time = datetime.fromisoformat(ts)
        return datetime.now() - cached_time < self.cache_ttl

    def get_stock_intelligence(self, ticker: str, buy_price: float, current_price: float) -> str:
        """
        Returns a rich text summary of the stock's 3-year history
        ready to paste into the AI council prompt.
        """
        if self._cache_valid(ticker):
            logger.debug(f"  Using cached intelligence for {ticker}")
            return self.cache[ticker]["summary"]

        try:
            summary = self._analyze(ticker, buy_price, current_price)
            self.cache[ticker] = {
                "summary": summary,
                "cached_at": datetime.now().isoformat()
            }
            self._save_cache()
            return summary
        except Exception as e:
            logger.debug(f"Intelligence fetch failed for {ticker}: {e}")
            return f"{ticker}: historical data unavailable"

    def _analyze(self, ticker: str, buy_price: float, current_price: float) -> str:
        # Fetch 3 years of daily data
        ns_ticker = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
        df = yf.download(ns_ticker, period="3y", progress=False, auto_adjust=True)

        if df.empty or len(df) < 50:
            return f"{ticker}: insufficient history"

        close = df["Close"].squeeze()
        close = close.dropna()

        now_price    = float(close.iloc[-1])
        price_3y_ago = float(close.iloc[0])
        price_1y_ago = float(close.iloc[-252]) if len(close) >= 252 else price_3y_ago
        price_6m_ago = float(close.iloc[-126]) if len(close) >= 126 else price_3y_ago
        price_3m_ago = float(close.iloc[-63])  if len(close) >= 63  else price_3y_ago

        # Returns
        ret_3y = ((now_price - price_3y_ago) / price_3y_ago) * 100
        ret_1y = ((now_price - price_1y_ago) / price_1y_ago) * 100
        ret_6m = ((now_price - price_6m_ago) / price_6m_ago) * 100
        ret_3m = ((now_price - price_3m_ago) / price_3m_ago) * 100

        # 52-week range
        high_52w = float(close.iloc[-252:].max()) if len(close) >= 252 else float(close.max())
        low_52w  = float(close.iloc[-252:].min()) if len(close) >= 252 else float(close.min())
        pct_from_high = ((current_price - high_52w) / high_52w) * 100
        pct_from_low  = ((current_price - low_52w)  / low_52w)  * 100

        # Monthly seasonality: average return per calendar month
        df_monthly = close.resample("ME").last().pct_change() * 100
        monthly_avg = df_monthly.groupby(df_monthly.index.month).mean()
        current_month = datetime.now().month
        this_month_hist = monthly_avg.get(current_month, 0)

        # Best 3-month period in the last year
        returns_3m_rolling = close.pct_change(63).iloc[-252:] * 100
        best_3m = float(returns_3m_rolling.max()) if not returns_3m_rolling.empty else 0

        # Drawdown recovery: avg days to recover from -5% dip
        recovery_days = self._estimate_recovery_days(close)

        # Trend direction
        sma50  = float(close.iloc[-50:].mean())
        sma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else sma50
        if now_price > sma50 > sma200:
            trend = "STRONG UPTREND"
        elif now_price > sma50:
            trend = "UPTREND"
        elif now_price < sma50 < sma200:
            trend = "STRONG DOWNTREND"
        else:
            trend = "SIDEWAYS"

        # Momentum score (0-100)
        recent_ret = ((now_price - float(close.iloc[-20])) / float(close.iloc[-20])) * 100
        momentum = "STRONG" if recent_ret > 3 else "MODERATE" if recent_ret > 0 else "WEAK"

        # Position vs buy price
        entry_pnl = ((current_price - buy_price) / buy_price) * 100

        summary = (
            f"{ticker} 3Y INTELLIGENCE:\n"
            f"  Trend: {trend} | Momentum (20d): {momentum} ({recent_ret:+.1f}%)\n"
            f"  Returns: 3Y={ret_3y:+.0f}% | 1Y={ret_1y:+.0f}% | 6M={ret_6m:+.0f}% | 3M={ret_3m:+.0f}%\n"
            f"  52W range: Low={low_52w:.1f} High={high_52w:.1f} | vs High: {pct_from_high:+.1f}% | vs Low: {pct_from_low:+.1f}%\n"
            f"  Seasonality: {datetime.now().strftime('%B')} historically {this_month_hist:+.1f}% avg | Best 3M ever: +{best_3m:.0f}%\n"
            f"  Recovery: avg {recovery_days}d to recover from -5% dip\n"
            f"  Our entry: buy={buy_price:.2f} now={current_price:.2f} P&L={entry_pnl:+.1f}%"
        )

        logger.info(f"  📊 Intelligence ready: {ticker} | {trend} | 3Y={ret_3y:+.0f}%")
        return summary

    def _estimate_recovery_days(self, close: pd.Series, dip_threshold: float = -0.05) -> int:
        """Estimate average days to recover from a -5% dip"""
        try:
            arr = close.values.astype(float)
            recovery_list = []
            i = 1
            while i < len(arr) - 1:
                ret = (arr[i] - arr[i - 1]) / arr[i - 1]
                if ret <= dip_threshold:
                    entry_price = arr[i]
                    for j in range(i + 1, min(i + 60, len(arr))):
                        if arr[j] >= entry_price:
                            recovery_list.append(j - i)
                            break
                    i += 5
                else:
                    i += 1
            return int(np.mean(recovery_list)) if recovery_list else 14
        except Exception:
            return 14


def enrich_exit_prompt(positions: dict, regime_state: str) -> str:
    """
    Build a rich AI prompt with 3-year backtest intelligence for each position.
    Used by council.vote_exits() to make smarter HOLD/SELL decisions.
    """
    intel = StockIntelligence()
    lines = []

    for ticker, pos in positions.items():
        pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
        stock_intel = intel.get_stock_intelligence(ticker, pos.avg_cost, pos.current_price)
        lines.append(stock_intel)

    prompt = (
        f"Market regime: {regime_state}\n\n"
        f"BACKTEST INTELLIGENCE FOR EACH HELD STOCK (3-year analysis):\n\n"
        + "\n\n".join(lines) +
        "\n\nBased on the 3-year history and current trends above: "
        "For each stock, should we HOLD (trend intact, let profit run) or SELL (trend breaking)? "
        "Consider: Is current momentum strong? Is this a good month historically? "
        "Are we near 52W high (take some profit) or recovering from a dip (hold for recovery)? "
        "NEVER sell a winner just because profit is big — only sell if trend is reversing."
    )
    return prompt


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    intel = StockIntelligence()

    # Test on current holdings
    test_stocks = [
        ("PNBGILTS", 89.30, 94.47),   # +5.8% winner
        ("UTLSOLAR", 337.00, 343.50),  # +2.1% winner
        ("MMTC",     69.66, 68.00),    # -2.4% loser
        ("INFY",    1205.30, 1203.80), # -0.1% flat
    ]

    print("=" * 70)
    for ticker, buy, now in test_stocks:
        summary = intel.get_stock_intelligence(ticker, buy, now)
        print(summary)
        print()
