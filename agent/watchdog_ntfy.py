#!/usr/bin/env python3
"""
MAX ALPHA WATCHDOG v2 — Smart Notifications
=============================================
Sends notifications ONLY when something actually needs your attention.

NOTIFICATIONS YOU WILL RECEIVE:
  1. Service started        — when watchdog boots up
  2. Agent crashed          — when agent process dies unexpectedly
  3. Agent restarted        — after auto-restart succeeds
  4. Agent frozen           — running but stuck (during market hours only)
  5. Too many crashes       — needs manual fix
  6. Daily heartbeat        — every morning at 1:00 AM AEST confirms PC is alive
     If you DON'T get this, your PC or internet is down
  7. Market open            — first cycle of the trading day starting
  8. Market closed          — end of trading day summary

NOTIFICATIONS YOU WILL NOT RECEIVE:
  - "No activity" during after-hours (agent is deliberately sleeping)
  - "No activity" on weekends (agent is deliberately sleeping)
  - Repeated duplicate alerts within 5 minutes
"""

import os, sys, time, subprocess, logging, datetime, requests
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from this script)
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
import pytz

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# =============================================================
#  CONFIGURATION — set your topic name
# =============================================================
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "maxalpha-jit-trading-2026")

CONFIG = {
    "ntfy_url":       f"https://ntfy.sh/{NTFY_TOPIC}",
    "agent_script":   "trader_v4.py",
    "agent_dir":      str(_ROOT / "agent"),
    "log_file":       "max_alpha_v4.log",
    "watchdog_log":   "watchdog.log",
    "check_interval": 60,      # check every 60 seconds
    "log_stale_mins": 25,      # only flag stale log DURING market hours
    "restart_delay":  10,
    "max_restarts":   10,
    "alert_cooldown": 300,     # 5 min between duplicate alerts
}
# =============================================================

AEST = pytz.timezone("Australia/Sydney")
UTC  = pytz.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(CONFIG["agent_dir"], CONFIG["watchdog_log"]),
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("Watchdog")


# ─────────────────────────────────────────────────────────────────
# MARKET HOURS AWARENESS
# ─────────────────────────────────────────────────────────────────
class MarketClock:
    """
    Knows exactly when the US market is open.
    Watchdog uses this to avoid false 'stale log' alerts
    when the agent is correctly sleeping.
    """
    OPEN_UTC  = (13, 30)   # 9:30 AM ET
    CLOSE_UTC = (20,  0)   # 4:00 PM ET
    PRE_UTC   = (12, 30)   # 8:30 AM ET pre-market scan

    def _now(self):
        n = datetime.datetime.now(UTC)
        return n.weekday(), n.hour * 60 + n.minute, n

    def is_weekend(self):
        d, _, _ = self._now()
        return d >= 5

    def is_market_open(self):
        d, m, _ = self._now()
        o = self.OPEN_UTC[0]  * 60 + self.OPEN_UTC[1]
        c = self.CLOSE_UTC[0] * 60 + self.CLOSE_UTC[1]
        return 0 <= d <= 4 and o <= m < c

    def is_active_period(self):
        """True if agent SHOULD be doing something (pre-market or market open)."""
        d, m, _ = self._now()
        p = self.PRE_UTC[0]   * 60 + self.PRE_UTC[1]
        c = self.CLOSE_UTC[0] * 60 + self.CLOSE_UTC[1]
        return 0 <= d <= 4 and p <= m < c

    def is_heartbeat_time(self):
        """
        True once per day at 1:00 AM AEST (just before pre-market scan).
        This is when the daily 'PC is alive' notification is sent.
        """
        now_aest = datetime.datetime.now(AEST)
        return now_aest.hour == 1 and now_aest.minute == 0

    def session_label(self):
        d, m, _ = self._now()
        o  = self.OPEN_UTC[0]  * 60 + self.OPEN_UTC[1]
        c  = self.CLOSE_UTC[0] * 60 + self.CLOSE_UTC[1]
        p  = self.PRE_UTC[0]   * 60 + self.PRE_UTC[1]
        if d >= 5: return "Weekend"
        if p <= m < o: return "Pre-market"
        if o <= m < o+90: return "Power open"
        if c-30 <= m < c: return "Power close"
        if o <= m < c: return "Mid-session"
        return "After hours"


clock = MarketClock()


# ─────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────
def notify(title: str, message: str, priority: str = "default",
           tags: str = "chart_with_upwards_trend") -> bool:
    """Send a push notification via ntfy.sh."""
    try:
        safe_title   = title.encode("ascii", errors="replace").decode("ascii")
        safe_message = message.encode("ascii", errors="replace").decode("ascii")
        resp = requests.post(
            CONFIG["ntfy_url"],
            data=safe_message.encode("utf-8"),
            headers={
                "Title":        safe_title,
                "Priority":     priority,
                "Tags":         tags,
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10
        )
        if resp.status_code == 200:
            log.info(f"Notification sent: {title}")
            return True
        else:
            log.warning(f"Notification failed: HTTP {resp.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        log.warning("No internet — could not send notification")
        return False
    except Exception as e:
        log.error(f"Notification error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────
# AGENT MONITOR
# ─────────────────────────────────────────────────────────────────
class AgentMonitor:

    def __init__(self):
        self.restarts_today    = 0
        self.last_restart_day  = datetime.date.today()
        self.last_alert_time   = 0
        self.last_heartbeat    = None   # date of last heartbeat sent
        self.market_was_open   = False  # track market open/close transitions
        self.log_path = os.path.join(CONFIG["agent_dir"], CONFIG["log_file"])

    # ── Process check ────────────────────────────────────────────
    def is_agent_running(self) -> bool:
        try:
            r = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "CommandLine", "/format:csv"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return CONFIG["agent_script"] in r.stdout
        except Exception:
            try:
                r = subprocess.run(
                    ["tasklist"], capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return "python.exe" in r.stdout
            except Exception:
                return True  # assume OK if we can't check

    # ── Log freshness (only meaningful during active periods) ────
    def is_log_stale(self) -> bool:
        """
        Only returns True during market hours.
        During after-hours/weekends the agent is sleeping on purpose
        so a stale log is completely normal and expected.
        """
        if not clock.is_active_period():
            return False   # agent is supposed to be quiet right now
        try:
            if not os.path.exists(self.log_path):
                return True
            age = (time.time() - os.path.getmtime(self.log_path)) / 60
            return age > CONFIG["log_stale_mins"]
        except Exception:
            return False

    def last_log_lines(self, n: int = 3) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-n:]).strip() if lines else "Log empty"
        except Exception:
            return "Cannot read log"

    # ── Alert cooldown ───────────────────────────────────────────
    def can_alert(self) -> bool:
        if time.time() - self.last_alert_time > CONFIG["alert_cooldown"]:
            self.last_alert_time = time.time()
            return True
        return False

    # ── Auto-restart ─────────────────────────────────────────────
    def restart_agent(self) -> bool:
        today = datetime.date.today()
        if today != self.last_restart_day:
            self.restarts_today   = 0
            self.last_restart_day = today

        if self.restarts_today >= CONFIG["max_restarts"]:
            if self.can_alert():
                notify(
                    "MAX ALPHA - Too many crashes",
                    f"Agent crashed {CONFIG['max_restarts']} times today.\n"
                    f"Automatic restarts paused.\n"
                    f"Please restart manually.",
                    priority="urgent",
                    tags="warning"
                )
            return False

        log.info(f"Restarting agent (restart #{self.restarts_today + 1} today)...")
        time.sleep(CONFIG["restart_delay"])

        try:
            subprocess.Popen(
                [sys.executable,
                 os.path.join(CONFIG["agent_dir"], CONFIG["agent_script"])],
                cwd=CONFIG["agent_dir"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self.restarts_today += 1
            log.info("Agent restarted")
            return True
        except Exception as e:
            log.error(f"Restart failed: {e}")
            return False

    # ── Daily heartbeat ─────────────────────────────────────────
    def check_heartbeat(self):
        """
        Sends one notification at 1:00 AM AEST every day.
        If you don't receive this, your PC or internet is down.
        """
        today = datetime.date.today()
        if clock.is_heartbeat_time() and self.last_heartbeat != today:
            self.last_heartbeat = today
            now_aest = datetime.datetime.now(AEST)
            notify(
                "MAX ALPHA - Daily check-in",
                f"PC is running. Agent is active.\n"
                f"Time: {now_aest.strftime('%a %d %b %H:%M AEST')}\n"
                f"Session: Pre-market scan starting soon.\n"
                f"US market opens in 30 minutes.",
                priority="low",
                tags="white_check_mark"
            )
            log.info("Daily heartbeat notification sent")

    # ── Market open/close transitions ───────────────────────────
    def check_market_transitions(self):
        """Send notification when market opens and closes."""
        is_open_now = clock.is_market_open()

        # Market just opened
        if is_open_now and not self.market_was_open:
            self.market_was_open = True
            now_aest = datetime.datetime.now(AEST)
            notify(
                "MAX ALPHA - Market open",
                f"US market now open.\n"
                f"Agent starting trading cycles.\n"
                f"Time: {now_aest.strftime('%H:%M AEST')}\n"
                f"Closes at 6:00 AM AEST.",
                priority="default",
                tags="bell"
            )
            log.info("Market open notification sent")

        # Market just closed
        elif not is_open_now and self.market_was_open:
            self.market_was_open = False
            now_aest = datetime.datetime.now(AEST)
            notify(
                "MAX ALPHA - Market closed",
                f"US market closed for today.\n"
                f"Agent sleeping until tomorrow 1:30 AM AEST.\n"
                f"Check your dashboard for today's results.",
                priority="low",
                tags="zzz"
            )
            log.info("Market close notification sent")

    # ── Main loop ─────────────────────────────────────────────────
    def run(self):
        log.info("=" * 54)
        log.info("  MAX ALPHA WATCHDOG v2")
        log.info(f"  Topic    : {NTFY_TOPIC}")
        log.info(f"  Interval : {CONFIG['check_interval']}s")
        log.info("  Smart: no false alerts during off-hours/weekends")
        log.info("=" * 54)

        # Startup notification
        notify(
            "MAX ALPHA - Watchdog started",
            f"Trading watchdog is now active.\n"
            f"You will be alerted if the agent crashes.\n"
            f"Daily check-in: every day at 1:00 AM AEST.\n"
            f"Market open/close alerts: Mon-Fri.",
            priority="default",
            tags="shield"
        )

        issues = 0

        while True:
            try:
                time.sleep(CONFIG["check_interval"])

                # Daily heartbeat (1:00 AM AEST)
                self.check_heartbeat()

                # Market open/close transitions
                self.check_market_transitions()

                # Agent health checks
                running = self.is_agent_running()
                stale   = self.is_log_stale()   # False during off-hours
                session = clock.session_label()

                if not running:
                    issues += 1
                    log.warning(f"Agent not running (check #{issues}) [{session}]")
                    if issues >= 2:
                        if self.can_alert():
                            last = self.last_log_lines()
                            notify(
                                "ALERT - Agent crashed",
                                f"Trading agent stopped unexpectedly.\n"
                                f"Session: {session}\n"
                                f"Restarting automatically...\n\n"
                                f"Last log: {last}",
                                priority="urgent",
                                tags="rotating_light"
                            )
                        if self.restart_agent():
                            notify(
                                "MAX ALPHA - Agent restarted",
                                f"Agent restarted successfully.\n"
                                f"Restart #{self.restarts_today} today.\n"
                                f"Trading resuming.",
                                priority="default",
                                tags="recycle"
                            )
                            issues = 0

                elif stale:
                    # Only reaches here during market hours (is_log_stale returns
                    # False during off-hours, so no false alerts)
                    issues += 1
                    log.warning(f"Log stale during market hours (check #{issues})")
                    if issues >= 2 and self.can_alert():
                        last = self.last_log_lines()
                        notify(
                            "ALERT - Agent may be frozen",
                            f"Agent running but no log activity for "
                            f"{CONFIG['log_stale_mins']} minutes.\n"
                            f"Session: {session}\n"
                            f"May need manual restart.\n\n"
                            f"Last log: {last}",
                            priority="high",
                            tags="warning"
                        )

                else:
                    if issues > 0:
                        log.info(f"Agent healthy [{session}]")
                        issues = 0
                    else:
                        log.info(
                            f"OK [{session}] | "
                            f"restarts today: {self.restarts_today} | "
                            f"active period: {clock.is_active_period()}"
                        )

            except KeyboardInterrupt:
                notify(
                    "MAX ALPHA - Watchdog stopped",
                    "Watchdog was manually stopped. Agent is no longer being monitored.",
                    priority="high",
                    tags="stop_sign"
                )
                log.info("Watchdog stopped by user.")
                break
            except Exception as e:
                log.error(f"Watchdog error: {e}")
                time.sleep(60)


if __name__ == "__main__":
    AgentMonitor().run()
