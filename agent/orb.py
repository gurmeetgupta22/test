import datetime
import json
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

try:
    from .market_data import dhan_intraday_candles, plain_ticker
    from .settings import CONFIG, IST, log
except ImportError:
    if __package__:
        raise
    from market_data import dhan_intraday_candles, plain_ticker
    from settings import CONFIG, IST, log


@dataclass
class ORBSetup:
    direction: str
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    orb_high: float
    orb_low: float
    orb_range: float
    hard_exit_time: str
    notes: str


class ORBStrategy:
    """Opening Range Breakout strategy translated from the supplied Pine notes."""

    SYMBOL_MAP_FILE = "symbol_map.json"

    def __init__(self):
        self._symbol_map: Dict[str, str] = self._load_symbol_map()

    def _load_symbol_map(self) -> Dict[str, str]:
        try:
            with open(self.SYMBOL_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(k).upper(): str(v) for k, v in data.items()}
        except Exception:
            return {}

    def evaluate(self, ticker: str) -> Optional[ORBSetup]:
        if not CONFIG.get("orb_enabled", True):
            return None

        now = datetime.datetime.now(IST)
        if now.weekday() >= 5:
            return None

        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        orb_end = now.replace(
            hour=CONFIG["orb_end_hour"], minute=CONFIG["orb_end_min"],
            second=0, microsecond=0,
        )
        hard_exit = now.replace(
            hour=CONFIG["orb_hard_exit_hour"], minute=CONFIG["orb_hard_exit_min"],
            second=0, microsecond=0,
        )
        entry_start = now.replace(
            hour=CONFIG["orb_entry_start_hour"], minute=CONFIG["orb_entry_start_min"],
            second=0, microsecond=0,
        )
        entry_end = now.replace(
            hour=CONFIG["orb_entry_end_hour"], minute=CONFIG["orb_entry_end_min"],
            second=0, microsecond=0,
        )
        if now < max(orb_end, entry_start) or now >= min(hard_exit, entry_end):
            return None

        security_id = self._symbol_map.get(plain_ticker(ticker))
        if not security_id:
            return None

        candles = dhan_intraday_candles(
            security_id=security_id,
            access_token=CONFIG["dhan_access_token"],
            from_dt=market_open - datetime.timedelta(minutes=5),
            to_dt=min(now, hard_exit),
            interval=CONFIG["orb_interval"],
        )
        if candles.empty:
            return None

        return self._setup_from_candles(candles, market_open, orb_end, hard_exit, entry_start, entry_end)

    def _setup_from_candles(
        self,
        candles: pd.DataFrame,
        market_open: datetime.datetime,
        orb_end: datetime.datetime,
        hard_exit: datetime.datetime,
        entry_start: Optional[datetime.datetime] = None,
        entry_end: Optional[datetime.datetime] = None,
    ) -> Optional[ORBSetup]:
        candles = candles.sort_index()
        orb = candles[(candles.index >= market_open) & (candles.index < orb_end)]
        if orb.empty:
            return None

        orb_high = float(orb["high"].max())
        orb_low = float(orb["low"].min())
        orb_range = orb_high - orb_low
        last_orb_close = float(orb["close"].iloc[-1])
        min_abs = float(CONFIG["orb_min_range"])
        min_pct = last_orb_close * float(CONFIG["orb_min_range_pct"])
        if orb_range < min(min_abs, min_pct):
            return None

        if CONFIG["orb_gap_filter"] and len(candles) > len(orb):
            first_open = float(orb["open"].iloc[0])
            prior = candles[candles.index < market_open]
            prev_close = float(prior["close"].iloc[-1]) if not prior.empty else first_open
            gap_pct = abs((first_open - prev_close) / prev_close) if prev_close else 0.0
            if gap_pct < float(CONFIG["orb_min_gap_pct"]):
                return None

        after_orb = candles[(candles.index >= orb_end) & (candles.index < hard_exit)].copy()
        if len(after_orb) < 2:
            return None

        prev_close = candles["close"].shift(1)
        true_range = pd.concat([
            candles["high"] - candles["low"],
            (candles["high"] - prev_close).abs(),
            (candles["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_lookback = int(CONFIG["orb_atr_lookback"])
        candles = candles.copy()
        candles["atr"] = true_range.rolling(atr_lookback, min_periods=2).mean()
        candles["atr_avg"] = candles["atr"].rolling(atr_lookback, min_periods=2).mean()
        candles["ema_fast"] = candles["close"].ewm(span=int(CONFIG["orb_ema_fast"]), adjust=False).mean()
        candles["ema_slow"] = candles["close"].ewm(span=int(CONFIG["orb_ema_slow"]), adjust=False).mean()
        typical = (candles["high"] + candles["low"] + candles["close"]) / 3.0
        vol_sum = candles["volume"].cumsum().replace(0, pd.NA)
        candles["vwap"] = (typical * candles["volume"]).cumsum() / vol_sum
        candles["vol_avg"] = candles["volume"].rolling(
            int(CONFIG["orb_volume_lookback"]), min_periods=1
        ).mean()

        start = entry_start or orb_end
        end = entry_end or hard_exit
        after_orb = candles[(candles.index >= max(orb_end, start)) & (candles.index < min(hard_exit, end))]
        for i in range(1, len(after_orb)):
            prev = after_orb.iloc[i - 1]
            cur = after_orb.iloc[i]
            close = float(cur["close"])
            open_price = float(cur["open"])
            high = float(cur["high"])
            low = float(cur["low"])
            volume = float(cur["volume"] or 0)
            vol_avg = float(cur["vol_avg"] or 0)
            vwap = float(cur["vwap"] or close)
            body_pct = abs(close - open_price) / max(0.01, high - low)
            atr = float(cur["atr"] or 0)
            atr_avg = float(cur["atr_avg"] or 0)
            ema_fast = float(cur["ema_fast"] or close)
            ema_slow = float(cur["ema_slow"] or close)

            vol_ok = (not CONFIG["orb_volume_filter"]) or (volume > vol_avg)
            long_vwap_ok = (not CONFIG["orb_vwap_filter"]) or close > vwap
            short_vwap_ok = (not CONFIG["orb_vwap_filter"]) or close < vwap
            atr_ok = (not CONFIG["orb_atr_filter"]) or (atr_avg <= 0 or atr >= atr_avg * float(CONFIG["orb_atr_expansion"]))
            long_ema_ok = (not CONFIG["orb_ema_filter"]) or close > ema_fast > ema_slow
            short_ema_ok = (not CONFIG["orb_ema_filter"]) or close < ema_fast < ema_slow
            body_ok = body_pct >= float(CONFIG["orb_min_body_pct"])
            breakout_buffer = orb_range * float(CONFIG["orb_breakout_buffer"])

            if (
                close > orb_high + breakout_buffer
                and float(prev["close"]) <= orb_high
                and vol_ok and long_vwap_ok and atr_ok and long_ema_ok and body_ok
            ):
                stop = orb_low - (orb_range * float(CONFIG["orb_stop_buffer"]))
                t1 = close + orb_range * float(CONFIG["orb_t1_mult"])
                t2 = close + orb_range * float(CONFIG["orb_t2_mult"])
                return ORBSetup(
                    direction="LONG",
                    entry=round(close, 4),
                    stop_loss=round(stop, 4),
                    target_1=round(t1, 4),
                    target_2=round(t2, 4),
                    orb_high=round(orb_high, 4),
                    orb_low=round(orb_low, 4),
                    orb_range=round(orb_range, 4),
                    hard_exit_time=hard_exit.strftime("%H:%M"),
                    notes="ORB long: close broke ORB high with VWAP and volume confirmation.",
                )

            if (
                close < orb_low - breakout_buffer
                and float(prev["close"]) >= orb_low
                and vol_ok and short_vwap_ok and atr_ok and short_ema_ok and body_ok
            ):
                log.info("  ORB short breakdown detected; equity short entries are not opened by this long-only scanner.")
                return ORBSetup(
                    direction="SHORT",
                    entry=round(close, 4),
                    stop_loss=round(orb_high + (orb_range * float(CONFIG["orb_stop_buffer"])), 4),
                    target_1=round(close - orb_range * float(CONFIG["orb_t1_mult"]), 4),
                    target_2=round(close - orb_range * float(CONFIG["orb_t2_mult"]), 4),
                    orb_high=round(orb_high, 4),
                    orb_low=round(orb_low, 4),
                    orb_range=round(orb_range, 4),
                    hard_exit_time=hard_exit.strftime("%H:%M"),
                    notes="ORB short: close broke ORB low with VWAP and volume confirmation.",
                )

        return None
