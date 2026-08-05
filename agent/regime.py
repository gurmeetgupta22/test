import math

import numpy as np

try:
    from .market_data import tradingview_quote, tradingview_scan
    from .models import MarketRegime
    from .settings import CONFIG, REGIME_ALLOCATIONS, REGIME_BREADTH_UNIVERSE, log
except ImportError:
    if __package__:
        raise
    from market_data import tradingview_quote, tradingview_scan
    from models import MarketRegime
    from settings import CONFIG, REGIME_ALLOCATIONS, REGIME_BREADTH_UNIVERSE, log

class RegimeEngine:
    """
    Analyses NIFTY, India VIX proxy, market breadth, and momentum to determine
    whether we're in a BULL, NEUTRAL, BEAR, or CRASH environment.
    This drives how capital is allocated across the three tiers.
    """

    def detect(self) -> MarketRegime:
        try:
            return self._analyse()
        except Exception as e:
            log.warning(f"Regime detection failed ({e}), defaulting to NEUTRAL")
            return self._default_neutral()

    def _analyse(self) -> MarketRegime:
        score_components = []
        reasoning_parts = []

        # NSE benchmark trend/momentum (NIFTY 50) from TradingView scanner.
        nifty = tradingview_quote(
            "NSE:NIFTY",
            ["close", "RSI", "SMA20", "SMA50", "Perf.1M", "Recommend.All"],
        )
        if not nifty:
            return self._default_neutral()

        sma20 = float(nifty.get("SMA20") or 0)
        sma50 = float(nifty.get("SMA50") or 0)
        nifty_price = float(nifty.get("close") or 0)
        nifty_rsi = float(nifty.get("RSI") or 50.0)

        # Trend
        if nifty_price > sma20 > sma50:
            trend = "uptrend"
            trend_score = 0.8
        elif nifty_price < sma20 < sma50:
            trend = "downtrend"
            trend_score = 0.2
        else:
            trend = "sideways"
            trend_score = 0.5
        score_components.append(trend_score)
        reasoning_parts.append(f"NIFTY {trend} (RSI {nifty_rsi:.0f})")

        # 1-month momentum from TradingView. Used as 20-session proxy.
        nifty_momentum = float(nifty.get("Perf.1M") or 0)
        mom_score = 0.5 + min(0.4, max(-0.4, nifty_momentum / 20))
        score_components.append(mom_score)
        reasoning_parts.append(f"NIFTY 20d momentum {nifty_momentum:+.1f}%")

        # India VIX / volatility proxy
        indiavix = tradingview_quote("NSE:INDIAVIX", ["close", "SMA20"])
        vix_score = 0.5
        vix_level = "normal"
        if indiavix:
            vix_now = float(indiavix.get("close") or 0)
            vix_avg = float(indiavix.get("SMA20") or vix_now or 0)
            vix_ratio = vix_now / vix_avg if vix_avg > 0 else 1.0
            if vix_ratio > 1.5:
                vix_level = "extreme"
                vix_score = 0.05
            elif vix_ratio > 1.2:
                vix_level = "high"
                vix_score = 0.25
            elif vix_ratio > 1.0:
                vix_level = "elevated"
                vix_score = 0.45
            else:
                vix_level = "low"
                vix_score = 0.75
        else:
            vix_level = "normal"
            vix_score = 0.50
        score_components.append(vix_score)
        reasoning_parts.append(f"Volatility {vix_level}")

        # Breadth: share of NSE leaders above 50 DMA
        rows = tradingview_scan(REGIME_BREADTH_UNIVERSE, ["close", "SMA50"])
        breadth_hits = 0
        breadth_total = 0
        for row in rows.values():
            px = float(row.get("close") or 0)
            sma50_s = float(row.get("SMA50") or 0)
            if px <= 0 or sma50_s <= 0:
                continue
            breadth_total += 1
            if px > sma50_s:
                breadth_hits += 1

        breadth = 0.5
        if breadth_total > 0:
            breadth = breadth_hits / breadth_total
            breadth_score = 0.2 + (breadth * 0.8)  # 0%=>0.2, 100%=>1.0
            score_components.append(breadth_score)
            reasoning_parts.append(f"Breadth {breadth_hits}/{breadth_total} above 50DMA")

        # RSI score
        rsi_score = 0.5
        if nifty_rsi > 70:   rsi_score = 0.3
        elif nifty_rsi > 60: rsi_score = 0.65
        elif nifty_rsi > 45: rsi_score = 0.75
        elif nifty_rsi > 35: rsi_score = 0.45
        else:                rsi_score = 0.20
        score_components.append(rsi_score)

        # Composite score
        composite = sum(score_components) / len(score_components)

        if composite >= CONFIG["bull_threshold"]:
            state = "BULL"
        elif composite <= CONFIG["crash_threshold"]:
            state = "CRASH"
        elif composite <= CONFIG["bear_threshold"]:
            state = "BEAR"
        else:
            state = "NEUTRAL"

        alloc = REGIME_ALLOCATIONS[state]
        reasoning = " | ".join(reasoning_parts) + f" | Score: {composite:.2f}"

        return MarketRegime(
            state=state, score=round(composite, 3),
            spy_trend=trend, spy_rsi=round(nifty_rsi, 1),
            vix_level=vix_level, breadth=round(breadth, 3),
            momentum=round(nifty_momentum, 2),
            allocation=alloc, reasoning=reasoning
        )

    def _default_neutral(self) -> MarketRegime:
        return MarketRegime(
            state="NEUTRAL", score=0.5,
            spy_trend="sideways", spy_rsi=50.0,
            vix_level="normal", breadth=0.5, momentum=0.0,
            allocation=REGIME_ALLOCATIONS["NEUTRAL"],
            reasoning="Default NEUTRAL  NIFTY data unavailable"
        )
