import datetime
from typing import Tuple

try:
    from .settings import CONFIG, IST
except ImportError:
    from settings import CONFIG, IST

class SmartScheduler:

    MARKET_OPEN  = (9, 15)   # IST
    MARKET_CLOSE = (15, 30)  # IST
    PRE_MARKET   = (9,  0)   # IST

    def __init__(self):
        self._last_pre = None

    def _now(self):
        n = datetime.datetime.now(IST)
        return n.weekday(), n.hour*60+n.minute, n

    def is_weekend(self):
        d,_,_ = self._now()
        return d >= 5

    def is_market_open(self):
        d,m,_ = self._now()
        o = self.MARKET_OPEN[0]*60+self.MARKET_OPEN[1]
        c = self.MARKET_CLOSE[0]*60+self.MARKET_CLOSE[1]
        return 0<=d<=4 and o<=m<c

    def is_premarket(self):
        d,m,_ = self._now()
        p = self.PRE_MARKET[0]*60+self.PRE_MARKET[1]
        o = self.MARKET_OPEN[0]*60+self.MARKET_OPEN[1]
        return 0<=d<=4 and p<=m<o

    def is_power_open(self):
        d,m,_ = self._now()
        o = self.MARKET_OPEN[0]*60+self.MARKET_OPEN[1]
        return 0<=d<=4 and o<=m<o+90

    def is_power_close(self):
        d,m,_ = self._now()
        c = self.MARKET_CLOSE[0]*60+self.MARKET_CLOSE[1]
        return 0<=d<=4 and c-30<=m<c

    def next_interval(self) -> Tuple[int, str, bool]:
        """Returns (sleep_secs, label, should_call_claude)"""
        if self.is_weekend():
            return self._secs_to_monday(), "Weekend â€” sleeping until Mon pre-market", False
        if self.is_premarket():
            today = datetime.datetime.now(IST).date()
            if self._last_pre != today:
                self._last_pre = today
                return 0, "Pre-market scan (no Claude)", False
            return self._secs_to_open(), "Waiting for market open", False
        if self.is_power_open():
            return CONFIG["power_open_interval"],  "Power open  â€” 5 min cycles", True
        if self.is_power_close():
            return CONFIG["power_close_interval"], "Power close â€” 5 min cycles", True
        if self.is_market_open():
            return CONFIG["mid_session_interval"], "Mid-session â€” 15 min cycles", True
        return self._secs_to_premarket(), "After hours â€” sleeping", False

    def _secs_to_open(self):
        n = datetime.datetime.now(IST)
        t = n.replace(hour=self.MARKET_OPEN[0], minute=self.MARKET_OPEN[1], second=0, microsecond=0)
        if n >= t: t += datetime.timedelta(days=1)
        return max(60, int((t-n).total_seconds()))

    def _secs_to_premarket(self):
        n = datetime.datetime.now(IST)
        t = n.replace(hour=self.PRE_MARKET[0], minute=self.PRE_MARKET[1], second=0, microsecond=0)
        if n >= t: t += datetime.timedelta(days=1)
        while t.weekday() >= 5: t += datetime.timedelta(days=1)
        return max(60, int((t-n).total_seconds()))

    def _secs_to_monday(self):
        n = datetime.datetime.now(IST)
        days = (7-n.weekday()) % 7 or 7
        t = (n + datetime.timedelta(days=days)).replace(
            hour=self.PRE_MARKET[0], minute=self.PRE_MARKET[1], second=0, microsecond=0)
        return max(60, int((t-n).total_seconds()))

    def status(self) -> str:
        ist = datetime.datetime.now(IST)
        ts   = ist.strftime("%a %d %b %H:%M IST")
        if self.is_weekend():     return f"{ts} â€” Weekend"
        if self.is_power_open():  return f"{ts} â€” POWER OPEN (5-min)"
        if self.is_power_close(): return f"{ts} â€” POWER CLOSE (5-min)"
        if self.is_market_open(): return f"{ts} â€” Market open (15-min)"
        if self.is_premarket():   return f"{ts} â€” Pre-market"
        return f"{ts} â€” After hours"
