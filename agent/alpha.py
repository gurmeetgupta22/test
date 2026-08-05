from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from .market_data import plain_ticker, tradingview_market_scan, tradingview_quote, tradingview_scan
    from .models import FundamentalView, ScoredStock
    from .orb import ORBStrategy
    from .settings import CONFIG, TIER1_SCAN_UNIVERSE, log
except ImportError:
    if __package__:
        raise
    from market_data import plain_ticker, tradingview_market_scan, tradingview_quote, tradingview_scan
    from models import FundamentalView, ScoredStock
    from orb import ORBStrategy
    from settings import CONFIG, TIER1_SCAN_UNIVERSE, log


TV_ALPHA_COLUMNS = [
    "name", "description", "close", "open", "high", "low", "volume",
    "relative_volume_10d_calc", "average_volume_10d_calc",
    "change", "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "RSI", "MACD.macd", "MACD.signal",
    "SMA20", "SMA50", "SMA200", "Recommend.All", "Volatility.D", "beta_1_year",
    "market_cap_basic", "price_earnings_ttm", "price_book_fq",
    "return_on_equity_fq", "debt_to_equity", "current_ratio",
    "earnings_per_share_diluted_yoy_growth_ttm", "gross_margin_ttm",
    "sector",
]

class Tier1Scanner:

    def scan(self) -> List[str]:
        if CONFIG.get("broad_market_scan", True):
            picked = self._broad_scan_for_tier(tier=1, limit=int(CONFIG.get("broad_scan_limit", 80)))
            if picked:
                return picked
        try:
            ranked: List[Tuple[float, str]] = []
            rows = tradingview_scan(
                TIER1_SCAN_UNIVERSE,
                ["close", "volume", "relative_volume_10d_calc", "change", "Perf.W"],
            )
            for ticker in TIER1_SCAN_UNIVERSE:
                try:
                    row = rows.get(plain_ticker(ticker), {})
                    if not row:
                        continue
                    px = float(row.get("close") or 0)
                    if not (20 <= px <= 250):  # Tier1 INR corridor
                        continue
                    volume = float(row.get("volume") or 0)
                    if volume <= 0:
                        continue
                    rel_vol = float(row.get("relative_volume_10d_calc") or 1.0)
                    c1 = float(row.get("change") or 0)
                    c5 = float(row.get("Perf.W") or 0)
                    score = (max(-5.0, c1) * 0.7) + (max(-8.0, c5) * 0.3) + (min(rel_vol, 4.0) * 8.0)
                    ranked.append((score, ticker))
                except Exception:
                    continue

            ranked.sort(key=lambda x: x[0], reverse=True)
            picked = [t for _, t in ranked[:30]]
            if picked:
                return picked
            return TIER1_SCAN_UNIVERSE[:20]
        except Exception as e:
            log.warning(f"NSE Tier1 scan error ({e})  using fallback list")
            return TIER1_SCAN_UNIVERSE[:20]

    def scan_tier2(self) -> List[str]:
        return self._broad_scan_for_tier(tier=2, limit=int(CONFIG.get("broad_scan_limit", 80)))

    def scan_tier3(self) -> List[str]:
        return self._broad_scan_for_tier(tier=3, limit=int(CONFIG.get("broad_scan_limit", 80)))

    def _broad_scan_for_tier(self, tier: int, limit: int) -> List[str]:
        try:
            sort_by = "market_cap_basic" if tier == 3 else "relative_volume_10d_calc"
            rows = tradingview_market_scan(
                ["name", "close", "volume", "relative_volume_10d_calc", "change", "Perf.W", "market_cap_basic"],
                limit=max(limit * 3, 120),
                types=("stock", "fund"),
                sort_by=sort_by,
                sort_order="desc",
            )
            ranked: List[Tuple[float, str]] = []
            seen = set()
            for ticker, row in rows.items():
                if not ticker or ticker in seen:
                    continue
                seen.add(ticker)
                px = float(row.get("close") or 0)
                volume = float(row.get("volume") or 0)
                min_volume = {
                    1: CONFIG["tier1_min_volume"],
                    2: CONFIG["tier2_min_volume"],
                    3: CONFIG["tier3_min_volume"],
                }[tier]
                if volume < min_volume:
                    continue
                mcap = float(row.get("market_cap_basic") or 0)
                if tier == 1 and not (20 <= px <= 250):
                    continue
                if tier == 2 and not (250 < px <= 1500):
                    continue
                if tier == 3 and px < 100:
                    continue
                if tier == 3 and 0 < mcap < 50_000_000_000:
                    continue
                rel_vol = float(row.get("relative_volume_10d_calc") or 1.0)
                c1 = float(row.get("change") or 0)
                c5 = float(row.get("Perf.W") or 0)
                liquidity_score = min(volume / 2_000_000, 3.0)
                momentum_score = max(-4.0, c1) * 0.5 + max(-6.0, c5) * 0.25
                size_bonus = min(mcap / 500_000_000_000, 1.0) if tier == 3 else 0.0
                score = min(rel_vol, 5.0) * 5.0 + liquidity_score + momentum_score + size_bonus
                ranked.append((score, ticker))
            ranked.sort(key=lambda x: x[0], reverse=True)
            picked = [t for _, t in ranked[:limit]]
            if picked:
                log.info(f"  Broad NSE scan tier {tier}: {len(picked)} liquid symbols selected")
            return picked
        except Exception as e:
            log.warning(f"Broad NSE tier {tier} scan failed ({e})")
            return []

class AlphaEngine:

    def __init__(self):
        self.orb = ORBStrategy()

    def score(self, ticker: str, tier: int) -> Optional[ScoredStock]:
        try:
            row = tradingview_quote(ticker, TV_ALPHA_COLUMNS)
            if not row:
                return None

            price = float(row.get("close") or 0)
            high = float(row.get("high") or price)
            low = float(row.get("low") or price)
            open_price = float(row.get("open") or price)
            if price <= 0:
                return None

            # Tier price range check
            if tier == 1 and not (20 <= price <= 250): return None
            if tier == 2 and not (250 < price <= 1500): return None
            if tier == 3 and price < 100: return None

            # Volume
            volume    = int(row.get("volume") or 0)
            avg_vol   = int(row.get("average_volume_10d_calc") or volume or 0)
            rel_vol   = float(row.get("relative_volume_10d_calc") or (volume / avg_vol if avg_vol > 0 else 1.0))
            min_vol   = {1: CONFIG["tier1_min_volume"],
                         2: CONFIG["tier2_min_volume"],
                         3: CONFIG["tier3_min_volume"]}[tier]
            if volume < min_vol: return None

            # Returns
            c1  = float(row.get("change") or 0)
            c5  = float(row.get("Perf.W") or 0)
            c20 = float(row.get("Perf.1M") or 0)
            c60 = float(row.get("Perf.3M") or 0)
            c120 = float(row.get("Perf.6M") or 0)

            # Gap
            prev_close = price / (1 + c1 / 100) if c1 > -99 else price
            gap = float((open_price - prev_close) / prev_close * 100) if prev_close else 0

            # TradingView technicals
            rsi = float(row.get("RSI") or 50.0)
            macd = float(row.get("MACD.macd") or 0)
            macd_signal = float(row.get("MACD.signal") or 0)
            macd_s = "bullish" if macd > macd_signal else "bearish"
            s20 = float(row.get("SMA20") or price)
            s50 = float(row.get("SMA50") or price)
            s200 = float(row.get("SMA200") or s50)
            trend = "uptrend" if price > s20 > s50 else ("downtrend" if price < s20 < s50 else "sideways")
            history_pattern, recovery_score, downtrend_risk = self._history_view(
                price=price, s20=s20, s50=s50, s200=s200,
                c1=c1, c5=c5, c20=c20, c60=c60, c120=c120,
                rsi=rsi, macd_signal=macd_s, rel_vol=rel_vol,
            )

            # TradingView snapshot does not expose Bollinger width in this scan.
            volatility = float(row.get("Volatility.D") or 0)
            bb_sq = bool(0 < volatility < 1.5)

            # 52w high
            near_hi = bool(c20 > 8 and price > s20)

            # Short squeeze
            short_pct = 0.0
            sq_score  = min(1.0, (min(rel_vol,5)/5)*0.5 + (max(0,c1)/10)*0.3 + (max(0,c5)/15)*0.2)

            # News
            headlines, has_news = [], False

            cat_score = self._catalyst_score(headlines)

            # Fundamentals (tier 2 & 3 care more)
            pe    = float(row.get("price_earnings_ttm") or 0)
            pb    = float(row.get("price_book_fq") or 0)
            div_y = 0.0
            mcap  = float(row.get("market_cap_basic") or 0)
            sector= str(row.get("sector") or "unknown").lower()
            debt_to_equity = float(row.get("debt_to_equity") or 0)
            current_ratio  = float(row.get("current_ratio") or 0)
            roe            = float(row.get("return_on_equity_fq") or 0)
            revenue_growth = 0.0
            earnings_growth= float(row.get("earnings_per_share_diluted_yoy_growth_ttm") or 0)
            fundamental = self._fundamental_view(
                tier=tier, pe=pe, pb=pb, div_y=div_y, debt_to_equity=debt_to_equity,
                current_ratio=current_ratio, roe=roe, revenue_growth=revenue_growth,
                earnings_growth=earnings_growth, market_cap=mcap,
            )

            # â”€â”€ Alpha scoring per tier â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if tier == 1:
                alpha = self._score_tier1(rel_vol, c1, c5, rsi, macd_s, cat_score, bb_sq, sq_score, gap, near_hi)
            elif tier == 2:
                alpha = self._score_tier2(rel_vol, c1, c5, c20, rsi, macd_s, trend, cat_score, pe, mcap, fundamental.score)
            else:
                alpha = self._score_tier3(rel_vol, c1, c20, rsi, macd_s, trend, div_y, pe, near_hi, fundamental.score)

            strategy_setup = ""
            strategy_direction = ""
            strategy_stop_loss = 0.0
            strategy_target_1 = 0.0
            strategy_take_profit = 0.0
            strategy_hard_exit_time = ""
            strategy_notes = ""
            orb = self.orb.evaluate(ticker)
            if orb and orb.direction == "LONG":
                strategy_setup = "orb_long_breakout"
                strategy_direction = "LONG"
                strategy_stop_loss = orb.stop_loss
                strategy_target_1 = orb.target_1
                strategy_take_profit = orb.target_2
                strategy_hard_exit_time = orb.hard_exit_time
                strategy_notes = (
                    f"{orb.notes} ORB high {orb.orb_high}, low {orb.orb_low}, "
                    f"range {orb.orb_range}; T1 {orb.target_1}, T2 {orb.target_2}."
                )
                alpha = min(1.0, alpha + float(CONFIG["orb_alpha_boost"]))
            elif orb and orb.direction == "SHORT":
                strategy_setup = "orb_short_breakdown"
                strategy_direction = "SHORT"
                strategy_stop_loss = orb.stop_loss
                strategy_target_1 = orb.target_1
                strategy_take_profit = orb.target_2
                strategy_hard_exit_time = orb.hard_exit_time
                strategy_notes = (
                    f"{orb.notes} ORB high {orb.orb_high}, low {orb.orb_low}, "
                    f"range {orb.orb_range}."
                )
                alpha = max(0.0, alpha - 0.10)

            return ScoredStock(
                ticker=ticker, tier=tier, price=round(price,4),
                alpha_score=round(alpha,3), rsi=round(rsi,1),
                macd_signal=macd_s, trend=trend,
                rel_volume=round(rel_vol,2), volume=volume,
                change_1d=round(c1,2), change_5d=round(c5,2),
                gap_pct=round(gap,2), squeeze_score=round(sq_score,3),
                bb_squeeze=bb_sq, near_52w_high=near_hi,
                has_news=has_news, news_headlines=headlines,
                short_pct=round(short_pct,1), market_cap=mcap,
                sector=sector, pe_ratio=round(pe,1), dividend_yield=round(div_y,2),
                price_to_book=round(pb, 2), debt_to_equity=round(debt_to_equity, 2),
                current_ratio=round(current_ratio, 2), roe=round(roe, 2),
                revenue_growth=round(revenue_growth, 2),
                earnings_growth=round(earnings_growth, 2),
                fundamental=fundamental,
                strategy_setup=strategy_setup,
                strategy_direction=strategy_direction,
                strategy_stop_loss=strategy_stop_loss,
                strategy_target_1=strategy_target_1,
                strategy_take_profit=strategy_take_profit,
                strategy_hard_exit_time=strategy_hard_exit_time,
                strategy_notes=strategy_notes,
                history_pattern=history_pattern,
                recovery_score=round(recovery_score, 3),
                downtrend_risk=round(downtrend_risk, 3),
            )
        except Exception as e:
            log.debug(f"TradingView alpha score failed {ticker} tier{tier}: {e}")
            return None

    def _history_view(self, price, s20, s50, s200, c1, c5, c20, c60, c120, rsi, macd_signal, rel_vol):
        short_turn = c1 > -1.0 and c5 > 0 and price >= s20 and rsi >= 42
        base_recovery = c60 < -4 and c20 > -1 and c5 > 0 and price >= s20
        trend_continuation = price > s20 > s50 and c5 >= 0 and c20 >= 0
        falling = c5 < -2 and c20 < -4 and price < s20
        structural_downtrend = price < s20 < s50 and (c20 < 0 or c60 < 0)

        recovery_score = 0.0
        recovery_score += 0.22 if price >= s20 else 0.0
        recovery_score += 0.18 if s20 >= s50 else 0.06 if price >= s20 else 0.0
        recovery_score += 0.16 if c5 > 0 else 0.0
        recovery_score += 0.14 if c20 > 0 else 0.07 if c20 > -2 else 0.0
        recovery_score += 0.12 if 42 <= rsi <= 68 else 0.04 if 38 <= rsi <= 74 else 0.0
        recovery_score += 0.10 if macd_signal == "bullish" else 0.0
        recovery_score += 0.08 if rel_vol >= 1.0 else 0.0
        if base_recovery:
            recovery_score += 0.12
        recovery_score = max(0.0, min(1.0, recovery_score))

        downtrend_risk = 0.0
        downtrend_risk += 0.28 if price < s20 else 0.0
        downtrend_risk += 0.22 if s20 < s50 else 0.0
        downtrend_risk += 0.18 if c5 < 0 else 0.0
        downtrend_risk += 0.16 if c20 < -2 else 0.0
        downtrend_risk += 0.10 if rsi < 42 else 0.0
        downtrend_risk += 0.06 if macd_signal == "bearish" else 0.0
        downtrend_risk = max(0.0, min(1.0, downtrend_risk))

        if trend_continuation:
            pattern = "confirmed_uptrend"
        elif base_recovery or short_turn:
            pattern = "recovery_after_pullback"
        elif falling or structural_downtrend:
            pattern = "falling_downtrend"
        elif c120 > 0 and c60 < 0 and c5 > 0:
            pattern = "long_uptrend_pullback_recovering"
        else:
            pattern = "sideways_or_unproven"
        return pattern, recovery_score, downtrend_risk

    def _score_tier1(self, rv, c1, c5, rsi, macd, cat, bb_sq, sq, gap, hi52) -> float:
        """
        IMPROVED TIER1 SCORING (v2 - Better Confluence)
        Tier 1: Penny stocks - focus on momentum + volume confluence
        Returns stepped scores: 0.3/0.6/0.75/0.85 (not continuous 0.5-0.7)
        """
        checks = {
            'volume': False,
            'momentum': False,
            'rsi_valid': False,
            'macd_bullish': False,
            'catalyst': False,
        }
        
        # CHECK 1: Volume surge (must have 2x+ relative volume)
        if rv >= 2.0:
            checks['volume'] = True
        
        # CHECK 2: Momentum (1-day + 5-day both positive)
        if c1 > 0.5 and c5 > 0.0:
            checks['momentum'] = True
        elif c1 > 0.0 and c5 > 0.0:
            checks['momentum'] = True  # Partial credit
        
        # CHECK 3: RSI valid for entry (40-70 range)
        if 40 <= rsi <= 70:
            checks['rsi_valid'] = True
        
        # CHECK 4: MACD confirmation
        if macd == "bullish":
            checks['macd_bullish'] = True
        
        # CHECK 5: Catalyst score
        if cat >= 0.6:
            checks['catalyst'] = True
        
        # BONUS signals
        bonus = 0.0
        if bb_sq and sq > 0.6:
            bonus += 0.05  # Bollinger Squeeze
        if gap > 5:
            bonus += 0.05  # Gap
        if hi52:
            bonus += 0.03  # 52W High
        
        # DECISION LOGIC (stepped scoring)
        passed_checks = sum(1 for v in checks.values() if v)
        
        if passed_checks >= 4:
            alpha = 0.85  # STRONG BUY
        elif passed_checks == 3:
            alpha = 0.75  # BUY
        elif passed_checks == 2:
            alpha = 0.60  # MONITOR
        else:
            alpha = 0.30  # SKIP
        
        alpha = min(1.0, alpha + bonus)
        return round(alpha, 3)

    def _score_tier2(self, rv, c1, c5, c20, rsi, macd, trend, cat, pe, mcap, fundamental_score) -> float:
        """
        IMPROVED TIER2 SCORING (v2 - Better Confluence)
        Tier 2: Midcaps - balance momentum + fundamentals
        Returns stepped scores: 0.3/0.6/0.75/0.85
        """
        checks = {
            'trend': False,
            'momentum': False,
            'rsi_valid': False,
            'fundamentals': False,
            'volume': False,
        }
        
        # CHECK 1: Trend (uptrend required)
        if trend == "uptrend":
            checks['trend'] = True
        
        # CHECK 2: Momentum (5d + 20d positive)
        if c5 > 0.0 and c20 > 0.0:
            checks['momentum'] = True
        elif c5 > 0.0 or c20 > 0.0:
            checks['momentum'] = True  # Partial credit
        
        # CHECK 3: RSI (45-70 for tier2)
        if 45 <= rsi <= 70:
            checks['rsi_valid'] = True
        
        # CHECK 4: Fundamentals (PE + Quality score)
        if (0 < pe < 30 or fundamental_score > 0.65):
            checks['fundamentals'] = True
        elif (0 < pe < 50 or fundamental_score > 0.50):
            checks['fundamentals'] = True  # Partial credit
        
        # CHECK 5: Volume (1.5x+ for tier2)
        if rv >= 1.5:
            checks['volume'] = True
        
        # BONUS
        bonus = 0.0
        if macd == "bullish":
            bonus += 0.02
        if cat > 0.6:
            bonus += 0.03
        
        # DECISION
        passed_checks = sum(1 for v in checks.values() if v)
        
        if passed_checks >= 4:
            alpha = 0.85
        elif passed_checks == 3:
            alpha = 0.75
        elif passed_checks == 2:
            alpha = 0.60
        else:
            alpha = 0.30
        
        alpha = min(1.0, alpha + bonus)
        return round(alpha, 3)

    def _score_tier3(self, rv, c1, c20, rsi, macd, trend, div_y, pe, hi52, fundamental_score) -> float:
        """
        IMPROVED TIER3 SCORING (v2 - Better Confluence)
        Tier 3: Bluechips/ETFs - quality + safety focus
        Returns stepped scores: 0.3/0.6/0.75/0.85
        """
        checks = {
            'trend': False,
            'quality': False,
            'rsi_safe': False,
            'macd_bullish': False,
            'value': False,
        }
        
        # CHECK 1: Trend (must be uptrend or sideways)
        if trend == "uptrend":
            checks['trend'] = True
        elif trend == "sideways":
            checks['trend'] = True  # Partial credit for sideways
        
        # CHECK 2: Quality (dividend + fundamentals)
        if div_y > 2.0 and fundamental_score > 0.65:
            checks['quality'] = True
        elif div_y > 1.5 or fundamental_score > 0.60:
            checks['quality'] = True  # Partial credit
        
        # CHECK 3: RSI safe (35-65 for bluechip, less volatile)
        if 35 <= rsi <= 65:
            checks['rsi_safe'] = True
        
        # CHECK 4: MACD (should align)
        if macd == "bullish":
            checks['macd_bullish'] = True
        
        # CHECK 5: Valuation (reasonable PE)
        if 0 < pe < 30:
            checks['value'] = True
        elif 0 < pe < 40:
            checks['value'] = True  # Partial credit
        
        # DECISION
        passed_checks = sum(1 for v in checks.values() if v)
        
        if passed_checks >= 4:
            alpha = 0.80
        elif passed_checks == 3:
            alpha = 0.70
        elif passed_checks == 2:
            alpha = 0.55
        else:
            alpha = 0.30
        
        return round(alpha, 3)

    def _fundamental_view(self, tier: int, pe: float, pb: float, div_y: float,
                          debt_to_equity: float, current_ratio: float, roe: float,
                          revenue_growth: float, earnings_growth: float,
                          market_cap: float) -> FundamentalView:
        available = {
            "market_cap": market_cap,
            "pe": pe,
            "pb": pb,
            "debt_to_equity": debt_to_equity,
            "current_ratio": current_ratio,
            "roe": roe,
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "dividend_yield": div_y,
        }
        live_count = sum(1 for v in available.values() if v and not pd.isna(v))
        if live_count >= 8:
            confidence = "HIGH"
        elif live_count >= 6:
            confidence = "MODERATE"
        elif live_count > 0:
            confidence = "LOW"
        else:
            confidence = "VERY_LOW"

        valuation = "DATA_UNAVAILABLE"
        valuation_score = 0.45
        if pe > 0 or pb > 0:
            expensive = (pe > 60 if pe > 0 else False) or (pb > 8 if pb > 0 else False)
            cheap = (0 < pe < 25) and (0 < pb < 5 or pb == 0)
            valuation = "CHEAP" if cheap else ("EXPENSIVE" if expensive else "FAIR")
            valuation_score = {"CHEAP": 0.82, "FAIR": 0.65, "EXPENSIVE": 0.35}[valuation]

        growth = "DATA_UNAVAILABLE"
        growth_score = 0.45
        if revenue_growth or earnings_growth:
            if revenue_growth > 12 and earnings_growth > 12:
                growth, growth_score = "ACCELERATING", 0.85
            elif revenue_growth > 5 and earnings_growth > 0:
                growth, growth_score = "STEADY", 0.68
            elif revenue_growth > 0 or earnings_growth > 0:
                growth, growth_score = "SLOWING", 0.45
            else:
                growth, growth_score = "DECLINING", 0.22

        health = "DATA_UNAVAILABLE"
        health_score = 0.45
        if debt_to_equity or current_ratio:
            debt_ok = debt_to_equity <= 100 if debt_to_equity else True
            liquidity_ok = current_ratio >= 1.2 if current_ratio else True
            if debt_ok and liquidity_ok:
                health, health_score = "SAFE", 0.78
            elif debt_to_equity <= 200 or current_ratio >= 1.0:
                health, health_score = "WATCH", 0.52
            else:
                health, health_score = "RISK", 0.25

        returns = "DATA_UNAVAILABLE"
        returns_score = 0.45
        if roe:
            if roe > 15:
                returns, returns_score = "GOOD", 0.80
            elif roe >= 10:
                returns, returns_score = "AVERAGE", 0.58
            else:
                returns, returns_score = "WEAK", 0.25

        ownership = "DATA_UNAVAILABLE"
        ownership_score = 0.50

        weights = {
            1: (0.15, 0.30, 0.20, 0.20, 0.15),
            2: (0.20, 0.30, 0.20, 0.20, 0.10),
            3: (0.25, 0.20, 0.25, 0.25, 0.05),
        }[tier]
        score = (
            valuation_score * weights[0] +
            growth_score * weights[1] +
            health_score * weights[2] +
            returns_score * weights[3] +
            ownership_score * weights[4]
        )
        if confidence == "LOW":
            score *= 0.88
        elif confidence == "VERY_LOW":
            score *= 0.72

        if score >= 0.72 and confidence in ("HIGH", "MODERATE"):
            overall = "STRONG"
        elif score >= 0.50 and confidence != "VERY_LOW":
            overall = "MODERATE"
        else:
            overall = "WEAK"

        warnings = []
        if confidence in ("LOW", "VERY_LOW"):
            warnings.append("DATA UNAVAILABLE for several fundamental metrics; verify independently before investing.")
        if valuation == "EXPENSIVE":
            warnings.append("Valuation screen is expensive versus conservative thresholds.")
        if health == "RISK":
            warnings.append("Balance-sheet health screen is risky.")

        return FundamentalView(
            valuation=valuation, growth=growth, health=health, returns=returns,
            ownership=ownership, overall_quality=overall, data_confidence=confidence,
            score=round(max(0.0, min(1.0, score)), 3),
            sources={k: "TradingView scanner" for k, v in available.items() if v and not pd.isna(v)},
            warnings=warnings,
        )

    def _catalyst_score(self, headlines: List[str]) -> float:
        if not headlines: return 0.0
        high = ["fda","approval","contract","partnership","merger","acquisition",
                "revenue","earnings","beat","upgraded","buyout","breakthrough",
                "clinical","phase","granted","award","deal","raises"]
        mod  = ["launch","growth","expansion","positive","strong","results",
                "guidance","update","announces","signs"]
        text = " ".join(headlines).lower()
        s    = sum(0.15 for kw in high if kw in text)
        s   += sum(0.07 for kw in mod  if kw in text)
        return min(1.0, s)
