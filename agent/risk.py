import datetime
import json
import os
import sqlite3
from typing import Dict, Optional

import requests

try:
    from .models import Portfolio, StockSignal
    from .settings import CONFIG, IST, log
except ImportError:
    if __package__:
        raise
    from models import Portfolio, StockSignal
    from settings import CONFIG, IST, log


class AlertManager:
    def send(self, title: str, message: str):
        text = f"{title}\n{message}"
        token = CONFIG.get("telegram_bot_token", "")
        chat_id = CONFIG.get("telegram_chat_id", "")
        if token and chat_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                    timeout=10,
                )
                return
            except Exception as e:
                log.debug(f"Telegram alert failed: {e}")

        topic = CONFIG.get("ntfy_topic", "")
        if topic:
            try:
                requests.post(f"https://ntfy.sh/{topic}", data=text.encode("utf-8"), timeout=10)
            except Exception as e:
                log.debug(f"ntfy alert failed: {e}")


class TradeLedger:
    def __init__(self, path: Optional[str] = None):
        self.path = path or CONFIG["sqlite_trade_log"]
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        event TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        side TEXT,
                        qty INTEGER,
                        price REAL,
                        value REAL,
                        pnl REAL,
                        reason TEXT,
                        strategy TEXT
                    )
                    """
                )
        except Exception as e:
            log.warning(f"SQLite trade ledger unavailable: {e}")

    def record(self, event: str, ticker: str, side: str = "", qty: int = 0,
               price: float = 0.0, value: float = 0.0, pnl: float = 0.0,
               reason: str = "", strategy: str = ""):
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    INSERT INTO trades
                    (ts, event, ticker, side, qty, price, value, pnl, reason, strategy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.datetime.now(IST).isoformat(), event, ticker, side,
                        int(qty), float(price), float(value), float(pnl), reason, strategy,
                    ),
                )
        except Exception as e:
            log.debug(f"SQLite trade record failed: {e}")


class LearningEngine:
    def __init__(self, path: Optional[str] = None):
        self.path = path or CONFIG["learning_state_file"]
        self.state = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"tickers": {}, "strategies": {}, "teacher": {}}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log.debug(f"Learning state save failed: {e}")

    def _bucket(self, group: str, key: str) -> Dict:
        key = str(key or "unknown").upper() if group == "tickers" else str(key or "unknown")
        bucket = self.state.setdefault(group, {}).setdefault(key, {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0.0,
            "last_pnl": 0.0,
            "last_ts": "",
        })
        return bucket

    def record_outcome(self, ticker: str, strategy: str, pnl: float):
        for group, key in (("tickers", ticker), ("strategies", strategy or "unknown")):
            bucket = self._bucket(group, key)
            bucket["trades"] = int(bucket.get("trades") or 0) + 1
            if pnl > 0:
                bucket["wins"] = int(bucket.get("wins") or 0) + 1
            elif pnl < 0:
                bucket["losses"] = int(bucket.get("losses") or 0) + 1
            bucket["net_pnl"] = round(float(bucket.get("net_pnl") or 0.0) + pnl, 4)
            bucket["last_pnl"] = round(float(pnl), 4)
            bucket["last_ts"] = datetime.datetime.now(IST).isoformat()
        self._save()

    def adjustment(self, ticker: str, strategy: str) -> float:
        adj = 0.0
        for group, key in (("tickers", ticker), ("strategies", strategy or "unknown")):
            bucket = self._bucket(group, key)
            trades = int(bucket.get("trades") or 0)
            if trades < int(CONFIG["learning_min_trades"]):
                continue
            wins = int(bucket.get("wins") or 0)
            win_rate = wins / trades if trades else 0.0
            net_pnl = float(bucket.get("net_pnl") or 0.0)
            if win_rate < 0.40 or net_pnl < 0:
                adj -= float(CONFIG["learning_penalty"])
            elif win_rate >= 0.60 and net_pnl > 0:
                adj += float(CONFIG["learning_bonus"])
        return max(-0.30, min(0.08, adj))

    def reliability_penalty(self, ticker: str, strategy: str) -> float:
        penalty = 0.0
        min_trades = int(CONFIG["learning_min_trades"])
        min_win_rate = float(CONFIG.get("min_reliable_win_rate", 0.45))
        for group, key in (("tickers", ticker), ("strategies", strategy or "unknown")):
            bucket = self._bucket(group, key)
            trades = int(bucket.get("trades") or 0)
            if trades < min_trades:
                continue
            wins = int(bucket.get("wins") or 0)
            losses = int(bucket.get("losses") or 0)
            win_rate = wins / trades if trades else 0.0
            net_pnl = float(bucket.get("net_pnl") or 0.0)
            if losses >= 2 and wins == 0:
                penalty += 0.14
            elif win_rate < min_win_rate or net_pnl < 0:
                penalty += 0.08
        return min(0.24, penalty)

    def record_teacher_decision(self, ticker: str, strategy: str, source: str, score: float):
        if not ticker or source != "openrouter":
            return
        key = f"{str(ticker).upper()}::{strategy or 'unknown'}"
        bucket = self.state.setdefault("teacher", {}).setdefault(key, {
            "count": 0,
            "avg_score": 0.0,
            "last_score": 0.0,
            "last_ts": "",
            "source": source,
        })
        count = int(bucket.get("count") or 0) + 1
        old_avg = float(bucket.get("avg_score") or 0.0)
        bucket["count"] = count
        bucket["avg_score"] = round(old_avg + (float(score) - old_avg) / count, 4)
        bucket["last_score"] = round(float(score), 4)
        bucket["last_ts"] = datetime.datetime.now(IST).isoformat()
        bucket["source"] = source
        self._save()

    def teacher_adjustment(self, ticker: str, strategy: str) -> float:
        key = f"{str(ticker or '').upper()}::{strategy or 'unknown'}"
        bucket = self.state.setdefault("teacher", {}).get(key, {})
        count = int(bucket.get("count") or 0)
        if count <= 0:
            return 0.0
        avg_score = float(bucket.get("avg_score") or 0.0)
        confidence = min(1.0, count / max(1, int(CONFIG["learning_min_trades"])))
        if avg_score < 0.62:
            return 0.0
        bonus = float(CONFIG.get("teacher_learning_bonus", 0.025)) * confidence
        return max(0.0, min(float(CONFIG.get("teacher_learning_max", 0.06)), bonus))

    def should_skip(
        self,
        ticker: str,
        strategy: str,
        alpha_score: float = 0.0,
        trend: str = "",
        rel_volume: float = 0.0,
        rsi: float = 50.0,
    ) -> bool:
        # Learning is a caution system, not a blacklist. Bad outcomes are handled
        # through adjustment(), which raises the buy threshold and lowers conviction.
        return False


class RiskManager:
    def __init__(self, path: Optional[str] = None):
        self.path = path or CONFIG["risk_state_file"]
        self.alerts = AlertManager()
        self.state = self._load()
        self._roll_dates()

    def _load(self) -> Dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        today = datetime.datetime.now(IST).date().isoformat()
        return {
            "date": today,
            "week": self._week_key(),
            "daily_realized_pnl": 0.0,
            "daily_trades": 0,
            "daily_orb_trades": 0,
            "consecutive_losses": 0,
            "weekly_peak_equity": 0.0,
            "halt_reason": "",
            "cooldown_until": "",
        }

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log.warning(f"Could not save risk state: {e}")

    def _week_key(self) -> str:
        y, w, _ = datetime.datetime.now(IST).isocalendar()
        return f"{y}-W{w:02d}"

    def _roll_dates(self):
        today = datetime.datetime.now(IST).date().isoformat()
        week = self._week_key()
        changed = False
        if self.state.get("date") != today:
            self.state.update({
                "date": today,
                "daily_realized_pnl": 0.0,
                "daily_trades": 0,
                "daily_orb_trades": 0,
                "consecutive_losses": 0,
                "halt_reason": "",
                "cooldown_until": "",
            })
            changed = True
        if self.state.get("week") != week:
            self.state["week"] = week
            self.state["weekly_peak_equity"] = 0.0
            self.state["halt_reason"] = ""
            self.state["cooldown_until"] = ""
            changed = True
        if changed:
            self._save()

    def update_equity(self, portfolio: Portfolio):
        self._roll_dates()
        peak = float(self.state.get("weekly_peak_equity") or 0.0)
        if portfolio.total_usd > peak:
            self.state["weekly_peak_equity"] = round(portfolio.total_usd, 4)
            self._save()

    def status(self, portfolio: Portfolio) -> Dict:
        self.update_equity(portfolio)
        reasons = []
        profit_lock = float(CONFIG.get("max_daily_profit_lock", 0.0))
        daily_pnl = float(self.state.get("daily_realized_pnl") or 0.0)
        if profit_lock > 0 and daily_pnl >= profit_lock:
            reasons.append(f"daily profit locked ({daily_pnl:.2f})")
        if float(self.state.get("daily_realized_pnl") or 0.0) <= -float(CONFIG["max_daily_loss"]):
            reasons.append(f"daily loss limit hit ({self.state.get('daily_realized_pnl')})")
        if int(self.state.get("consecutive_losses") or 0) >= int(CONFIG["max_consecutive_losses"]):
            until = self.state.get("cooldown_until", "")
            try:
                if until and datetime.datetime.now(IST) < datetime.datetime.fromisoformat(until):
                    reasons.append(f"loss cooldown until {until[11:16]}")
                else:
                    self.state["consecutive_losses"] = 0
                    self.state["halt_reason"] = ""
                    self.state["cooldown_until"] = ""
                    self._save()
            except Exception:
                self.state["consecutive_losses"] = 0
                self.state["halt_reason"] = ""
                self.state["cooldown_until"] = ""
                self._save()
        peak = float(self.state.get("weekly_peak_equity") or 0.0)
        if peak > 0:
            drawdown = (peak - portfolio.total_usd) / peak
            if drawdown >= float(CONFIG["weekly_drawdown_limit"]):
                reasons.append(f"weekly drawdown {drawdown:.1%}")
        halt_reason = self.state.get("halt_reason", "")
        if halt_reason and not halt_reason.startswith("Paused after"):
            reasons.append(halt_reason)
        return {"can_trade": not reasons, "reasons": reasons, "state": dict(self.state)}

    def can_open(self, signal: StockSignal, portfolio: Portfolio) -> bool:
        status = self.status(portfolio)
        if not status["can_trade"]:
            log.warning(f"Risk halt blocks {signal.ticker}: {', '.join(status['reasons'])}")
            return False
        if signal.strategy != "orb_long_breakout" and self._past_last_new_entry_time():
            log.warning(f"Late-session entry blocked for {signal.ticker}")
            return False
        if self._recent_loss_for_ticker(signal.ticker):
            log.warning(f"Re-entry blocked for {signal.ticker}: recent realized loss cooldown")
            return False
        if signal.strategy == "orb_long_breakout":
            if int(self.state.get("daily_orb_trades") or 0) >= int(CONFIG["max_orb_trades_per_day"]):
                log.warning(f"ORB day-trade cap reached ({CONFIG['max_orb_trades_per_day']})")
                return False
            fixed_qty = int(CONFIG["orb_trade_lots"]) * int(CONFIG["orb_lot_size"])
            if fixed_qty > 0 and signal.qty != fixed_qty:
                signal.qty = fixed_qty
                signal.value_usd = round(signal.qty * signal.price, 2)
        if signal.value_usd > portfolio.deployable_cash:
            log.warning(
                f"Margin/cash check failed for {signal.ticker}: "
                f"need Rs{signal.value_usd:.0f}, deployable Rs{portfolio.deployable_cash:.0f}"
            )
            return False
        return True

    def _past_last_new_entry_time(self) -> bool:
        now = datetime.datetime.now(IST)
        current = now.hour * 60 + now.minute
        cutoff = int(CONFIG["last_new_entry_hour"]) * 60 + int(CONFIG["last_new_entry_min"])
        return current >= cutoff

    def _recent_loss_for_ticker(self, ticker: str) -> bool:
        cooldown_days = int(CONFIG.get("loss_reentry_cooldown_days", 1))
        if cooldown_days <= 0:
            return False
        since = datetime.datetime.now(IST) - datetime.timedelta(days=cooldown_days)
        try:
            with sqlite3.connect(CONFIG["sqlite_trade_log"]) as conn:
                row = conn.execute(
                    """
                    SELECT ts, pnl, reason
                    FROM trades
                    WHERE event='EXIT' AND ticker=? AND pnl < 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (ticker,),
                ).fetchone()
        except Exception as e:
            log.debug(f"Recent loss cooldown check failed: {e}")
            return False
        if not row:
            return False
        try:
            ts = datetime.datetime.fromisoformat(row[0])
            return ts >= since
        except Exception:
            return False

    def record_entry(self, signal: StockSignal):
        self._roll_dates()
        self.state["daily_trades"] = int(self.state.get("daily_trades") or 0) + 1
        if signal.strategy == "orb_long_breakout":
            self.state["daily_orb_trades"] = int(self.state.get("daily_orb_trades") or 0) + 1
        self._save()

    def record_exit(self, pnl: float, reason: str = ""):
        self._roll_dates()
        self.state["daily_realized_pnl"] = round(float(self.state.get("daily_realized_pnl") or 0.0) + pnl, 4)
        if pnl < 0:
            self.state["consecutive_losses"] = int(self.state.get("consecutive_losses") or 0) + 1
        elif pnl > 0:
            self.state["consecutive_losses"] = 0
        if int(self.state["consecutive_losses"]) >= int(CONFIG["max_consecutive_losses"]):
            cooldown_until = datetime.datetime.now(IST) + datetime.timedelta(minutes=int(CONFIG["loss_cooldown_minutes"]))
            self.state["cooldown_until"] = cooldown_until.isoformat()
            self.state["halt_reason"] = f"Paused after {self.state['consecutive_losses']} consecutive losses"
        if float(self.state["daily_realized_pnl"]) <= -float(CONFIG["max_daily_loss"]):
            self.state["halt_reason"] = f"Daily loss limit hit: Rs{self.state['daily_realized_pnl']:.2f}"
        self._save()
        if self.state.get("halt_reason"):
            self.alerts.send("Trading halted", f"{self.state['halt_reason']} | Last exit: {reason}")
