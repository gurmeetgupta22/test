"""
Smart Exit Manager - AI-Guided Position Management
Uses TRAILING STOP to let winners run, cut losers fast.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ExitManager:
    """
    Exit strategy:
    1. Hard stop-loss  (protect capital, cut losers fast)
    2. TRAILING stop   (let winners run to 20%, 40%, 50%!)
    3. Chart signals   (RSI/SMA breakdown)
    4. Time exit       (don't hold dead positions forever)
    5. AI learning     (self-optimizing rules)
    
    TRAILING STOP logic:
      - Once profit > min_profit_pct, activate trailing stop
      - Trail = peak_price × (1 - trail_pct)
      - If price drops below trail → SELL (locks profit)
      - If price keeps rising → trail moves up, NEVER sell!
    
    Example:
      Buy Rs 100 | Trail 8%
      Price → 120  trail = 110.4  (HOLD)
      Price → 150  trail = 138.0  (HOLD, profit +38% locked)
      Price → 200  trail = 184.0  (HOLD, profit +84% locked)
      Price → 185  trail = 184.0  → SELL! (+85% locked!) ✅
    """

    def __init__(self):
        self.config_file = Path("exit_rules.json")
        self.load_exit_rules()

    def load_exit_rules(self):
        if self.config_file.exists():
            with open(self.config_file) as f:
                self.rules = json.load(f)
        else:
            self.rules = self._default_rules()
            self.save_exit_rules()

    def _default_rules(self):
        return {
            "tier1": {
                "stop_loss_pct": 3.0,         # Hard stop: sell if -3% from entry
                "trail_activate_pct": 5.0,    # Activate trailing once +5% profit
                "trail_pct": 8.0,             # Trail 8% below peak (let it run!)
                "max_hold_days": 14,
                "rsi_overbought_threshold": 80,
                "description": "Penny stocks - trail 8%, activate at +5%"
            },
            "tier2": {
                "stop_loss_pct": 2.5,
                "trail_activate_pct": 4.0,
                "trail_pct": 7.0,
                "max_hold_days": 21,
                "rsi_overbought_threshold": 80,
                "description": "Midcaps - trail 7%, activate at +4%"
            },
            "tier3": {
                "stop_loss_pct": 2.0,
                "trail_activate_pct": 3.0,
                "trail_pct": 6.0,
                "max_hold_days": 30,
                "rsi_overbought_threshold": 78,
                "description": "Large-caps - trail 6%, activate at +3%"
            },
            "learning_enabled": True,
            "learning_phase": "collecting"
        }

    def save_exit_rules(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.rules, f, indent=2)

    def evaluate_exit(self, position, current_price, rsi, sma20, sma50,
                      days_held, previous_close, market_regime="NEUTRAL"):
        """
        Evaluate exit using trailing stop logic.

        Rules:
          Phase 1 (pnl < trail_activate_pct):
            - Hard stop only: sell if pnl <= -stop_loss_pct
            - Otherwise HOLD, let it grow

          Phase 2 (pnl >= trail_activate_pct):
            - Trailing stop ACTIVE
            - Trail = peak_price × (1 - trail_pct/100)
            - Sell ONLY if current_price < trail
            - Never sell just because pnl is high!
        """

        ticker    = position['ticker']
        tier      = position['tier']
        buy_price = position['buy_price']
        peak_price = position.get('peak_price', current_price)

        pnl_pct   = ((current_price - buy_price) / buy_price) * 100

        tier_name = f"tier{tier}"
        rules     = self.rules.get(tier_name, self.rules["tier3"])

        stop_loss_pct      = rules["stop_loss_pct"]
        trail_activate_pct = rules["trail_activate_pct"]
        trail_pct          = rules["trail_pct"]

        # ── HARD STOP LOSS (always active) ──────────────────────────────
        if pnl_pct <= -stop_loss_pct:
            logger.warning(f"❌ {ticker}: STOP LOSS | P&L {pnl_pct:+.2f}% <= -{stop_loss_pct}%")
            return {
                "should_exit": True,
                "reason": f"Stop loss: {pnl_pct:+.2f}%",
                "type": "loss",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }

        # ── TRAILING STOP (active once profit hits trail_activate_pct) ──
        if pnl_pct >= trail_activate_pct:
            trail_price = peak_price * (1 - trail_pct / 100)
            if current_price <= trail_price:
                locked_profit = ((trail_price - buy_price) / buy_price) * 100
                logger.info(f"🎯 {ticker}: TRAILING STOP | Peak {peak_price:.2f} → Trail {trail_price:.2f} | Locked {locked_profit:+.2f}%")
                return {
                    "should_exit": True,
                    "reason": f"Trailing stop: peak {peak_price:.2f} → trail {trail_price:.2f} | Locked {locked_profit:+.2f}%",
                    "type": "trail",
                    "exit_price": current_price,
                    "expected_pnl_pct": pnl_pct
                }
            else:
                # Still above trail — HOLD and let it run!
                logger.info(f"🚀 {ticker}: LETTING RUN | P&L {pnl_pct:+.2f}% | Trail at {trail_price:.2f} (price {current_price:.2f} safe)")
                return {
                    "should_exit": False,
                    "reason": f"TRAILING HOLD | P&L {pnl_pct:+.2f}% | Trail {trail_price:.2f} | Keep riding!",
                    "type": None,
                    "exit_price": None,
                    "expected_pnl_pct": pnl_pct
                }

        # ── CHART SIGNALS (oversold breakdown) ──────────────────────────
        if rsi < 30 and pnl_pct < 0:
            logger.warning(f"🔴 {ticker}: RSI BREAKDOWN | RSI {rsi:.0f} + loss {pnl_pct:+.2f}%")
            return {
                "should_exit": True,
                "reason": f"RSI breakdown: {rsi:.0f} with loss",
                "type": "chart",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }

        if current_price < sma20 and previous_close >= sma20 and pnl_pct < 0:
            logger.warning(f"🔴 {ticker}: SMA20 BREAK | {current_price:.2f} < SMA20 {sma20:.2f}")
            return {
                "should_exit": True,
                "reason": f"SMA20 break with loss",
                "type": "chart",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }

        # ── TIME EXIT (stale position) ───────────────────────────────────
        if days_held >= rules["max_hold_days"] and pnl_pct < 0:
            logger.warning(f"⏰ {ticker}: TIME EXIT | {days_held} days, loss {pnl_pct:+.2f}%")
            return {
                "should_exit": True,
                "reason": f"Max hold {days_held} days with loss",
                "type": "time",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }

        # ── HOLD ─────────────────────────────────────────────────────────
        if pnl_pct >= 0:
            msg = f"HOLD (growing) | P&L {pnl_pct:+.2f}% | Trail activates at +{trail_activate_pct}%"
        else:
            msg = f"HOLD | P&L {pnl_pct:+.2f}% | Stop at -{stop_loss_pct}%"

        return {
            "should_exit": False,
            "reason": msg,
            "type": None,
            "exit_price": None,
            "expected_pnl_pct": pnl_pct
        }

    def learn_from_trade(self, exit_data):
        """AI Learning: adjust rules from actual outcomes"""
        if not self.rules.get("learning_enabled"):
            return

        tier     = exit_data["tier"]
        tier_name = f"tier{tier}"
        pnl_pct  = exit_data["pnl_pct"]
        exit_type = exit_data["exit_type"]

        logger.info(f"🤖 LEARNING: {exit_data['ticker']} ({tier_name}) | {exit_type} | P&L: {pnl_pct:+.2f}%")

        if "learning_history" not in self.rules:
            self.rules["learning_history"] = {"tier1": [], "tier2": [], "tier3": []}

        self.rules["learning_history"][tier_name].append({
            "timestamp": datetime.now().isoformat(),
            "ticker": exit_data["ticker"],
            "exit_type": exit_type,
            "pnl_pct": pnl_pct,
            "days_held": exit_data.get("days_held", 0)
        })

        history = self.rules["learning_history"][tier_name]
        if len(history) % 10 == 0:
            self._optimize_tier_rules(tier_name)

    def _optimize_tier_rules(self, tier_name):
        history = self.rules["learning_history"][tier_name]
        recent  = history[-10:]

        profits = [t for t in recent if t["pnl_pct"] > 0]
        losses  = [t for t in recent if t["pnl_pct"] <= 0]
        win_rate = len(profits) / len(recent) * 100 if recent else 0
        avg_win  = sum(t["pnl_pct"] for t in profits) / len(profits) if profits else 0
        avg_loss = abs(sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0

        logger.info(f"📊 AI Optimize {tier_name}: WR {win_rate:.0f}% | Avg win {avg_win:+.2f}% | Avg loss -{avg_loss:.2f}%")

        rules = self.rules[tier_name]

        # Tighten stop if losing too much
        if win_rate < 50:
            rules["stop_loss_pct"] = max(1.5, rules["stop_loss_pct"] - 0.3)
            logger.info(f"  ↓ Stop tightened to {rules['stop_loss_pct']}%")

        # Loosen trail if winners are big (let them run more!)
        if avg_win > 10 and win_rate > 60:
            rules["trail_pct"] = min(12.0, rules["trail_pct"] + 1.0)
            logger.info(f"  ↑ Trail loosened to {rules['trail_pct']}% (big winners detected!)")

        self.save_exit_rules()

    def get_summary(self):
        summary = "📋 CURRENT EXIT RULES (Trailing Stop):\n"
        for tier in ["tier1", "tier2", "tier3"]:
            r = self.rules[tier]
            summary += f"\n{tier.upper()}: Hard stop -{r['stop_loss_pct']}% | Trail activates +{r['trail_activate_pct']}% | Trail {r['trail_pct']}% | Max {r['max_hold_days']} days\n"
        return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = ExitManager()

    print("\n=== TEST: WINNER (let it run!) ===")
    # Bought at 100, now at 130 (+30%), peak was 135
    r = mgr.evaluate_exit(
        position={"ticker": "TEST", "buy_price": 100, "qty": 10, "tier": 2, "buy_date": "2026-06-01", "peak_price": 135},
        current_price=130, rsi=65, sma20=120, sma50=110,
        days_held=4, previous_close=132
    )
    print(r["reason"])

    print("\n=== TEST: TRAILING STOP FIRED ===")
    # Bought at 100, peak 150, now dropped to 136 (below 8% trail of 150)
    r = mgr.evaluate_exit(
        position={"ticker": "TEST", "buy_price": 100, "qty": 10, "tier": 2, "buy_date": "2026-06-01", "peak_price": 150},
        current_price=136, rsi=55, sma20=130, sma50=120,
        days_held=6, previous_close=140
    )
    print(r["reason"])

    print("\n=== TEST: HARD STOP LOSS ===")
    # Bought at 100, now at 96.5 (-3.5% loss)
    r = mgr.evaluate_exit(
        position={"ticker": "TEST", "buy_price": 100, "qty": 10, "tier": 2, "buy_date": "2026-06-04", "peak_price": 102},
        current_price=96.5, rsi=38, sma20=99, sma50=97,
        days_held=1, previous_close=98
    )
    print(r["reason"])

    print("\n" + mgr.get_summary())


import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class ExitManager:
    """
    Manages exit decisions for positions using:
    1. Dynamic stop-loss (tier-based)
    2. Profit targets
    3. Chart signals (RSI/SMA breaks)
    4. Time-based exits
    5. AI learning for optimization
    """
    
    def __init__(self):
        self.config_file = Path("exit_rules.json")
        self.load_exit_rules()
        
    def load_exit_rules(self):
        """Load dynamic exit rules from file, or use defaults"""
        if self.config_file.exists():
            with open(self.config_file) as f:
                self.rules = json.load(f)
        else:
            self.rules = self._default_rules()
            self.save_exit_rules()
    
    def _default_rules(self):
        """Default exit rules by tier and market regime"""
        return {
            "tier1": {
                "stop_loss_pct": 3.0,
                "profit_target_pct": 5.0,
                "max_hold_days": 7,
                "rsi_overbought_threshold": 75,
                "description": "Penny stocks - higher volatility, larger stops"
            },
            "tier2": {
                "stop_loss_pct": 2.5,
                "profit_target_pct": 3.5,
                "max_hold_days": 10,
                "rsi_overbought_threshold": 75,
                "description": "Midcaps - moderate volatility"
            },
            "tier3": {
                "stop_loss_pct": 2.0,
                "profit_target_pct": 2.5,
                "max_hold_days": 14,
                "rsi_overbought_threshold": 70,
                "description": "Large-caps - lower volatility, tighter stops"
            },
            "learning_enabled": True,
            "learning_phase": "collecting"  # collecting -> optimizing -> stable
        }
    
    def save_exit_rules(self):
        """Persist rules to file for AI learning"""
        with open(self.config_file, 'w') as f:
            json.dump(self.rules, f, indent=2)
        logger.info(f"✅ Exit rules saved: {self.rules}")
    
    def evaluate_exit(self, position, current_price, rsi, sma20, sma50, 
                     days_held, previous_close, market_regime="NEUTRAL"):
        """
        Evaluate if position should be exited
        
        Args:
            position: dict with ticker, buy_price, qty, tier, buy_date
            current_price: float
            rsi: float (0-100)
            sma20: float
            sma50: float
            days_held: int
            previous_close: float
            market_regime: str (BULLISH/NEUTRAL/BEARISH)
        
        Returns:
            dict: {
                "should_exit": bool,
                "reason": str,
                "type": str (profit/loss/chart/time),
                "exit_price": float,
                "expected_pnl_pct": float
            }
        """
        
        ticker = position['ticker']
        tier = position['tier']
        buy_price = position['buy_price']
        
        # Calculate metrics
        pnl_pct = ((current_price - buy_price) / buy_price) * 100
        price_change = current_price - previous_close
        
        # Get tier-specific rules
        tier_name = f"tier{tier}"
        rules = self.rules.get(tier_name, self.rules["tier3"])
        
        # ==================== CHECK 1: PROFIT TARGET ====================
        if pnl_pct >= rules["profit_target_pct"]:
            logger.info(f"✅ {ticker}: PROFIT TARGET HIT | P&L: {pnl_pct:+.2f}% >= {rules['profit_target_pct']}%")
            return {
                "should_exit": True,
                "reason": f"Profit target reached: {pnl_pct:+.2f}%",
                "type": "profit",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # ==================== CHECK 2: STOP LOSS ====================
        if pnl_pct <= -rules["stop_loss_pct"]:
            logger.warning(f"❌ {ticker}: STOP LOSS HIT | P&L: {pnl_pct:+.2f}% <= -{rules['stop_loss_pct']}%")
            return {
                "should_exit": True,
                "reason": f"Stop loss triggered: {pnl_pct:+.2f}%",
                "type": "loss",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # ==================== CHECK 3: CHART BREAK (RSI + SMA) ====================
        # Overbought exit (take profit on strength)
        if rsi > rules["rsi_overbought_threshold"] and pnl_pct > 0:
            logger.info(f"🔴 {ticker}: OVERBOUGHT EXIT | RSI: {rsi:.0f} > {rules['rsi_overbought_threshold']}, P&L: {pnl_pct:+.2f}%")
            return {
                "should_exit": True,
                "reason": f"Overbought signal: RSI {rsi:.0f}",
                "type": "chart",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # Oversold exit (cut loss on weakness)
        if rsi < 30 and pnl_pct < 0 and pnl_pct > -rules["stop_loss_pct"]:
            logger.warning(f"🔴 {ticker}: OVERSOLD CUT LOSS | RSI: {rsi:.0f} < 30, P&L: {pnl_pct:+.2f}%")
            return {
                "should_exit": True,
                "reason": f"Oversold breakdown: RSI {rsi:.0f}",
                "type": "chart",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # SMA break (price breaks below SMA20)
        if current_price < sma20 and previous_close >= sma20 and pnl_pct < 0:
            logger.warning(f"🔴 {ticker}: SMA20 BREAK | Price: {current_price:.2f} < SMA20: {sma20:.2f}")
            return {
                "should_exit": True,
                "reason": f"SMA20 break: {current_price:.2f} < {sma20:.2f}",
                "type": "chart",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # ==================== CHECK 4: TIME EXIT ====================
        if days_held >= rules["max_hold_days"]:
            logger.warning(f"⏰ {ticker}: TIME EXIT | Held {days_held} days >= {rules['max_hold_days']}")
            return {
                "should_exit": True,
                "reason": f"Max hold time reached: {days_held} days",
                "type": "time",
                "exit_price": current_price,
                "expected_pnl_pct": pnl_pct
            }
        
        # No exit signal
        return {
            "should_exit": False,
            "reason": f"HOLD | P&L: {pnl_pct:+.2f}% (target: {rules['profit_target_pct']}%, stop: -{rules['stop_loss_pct']}%)",
            "type": None,
            "exit_price": None,
            "expected_pnl_pct": pnl_pct
        }
    
    def learn_from_trade(self, exit_data):
        """
        AI Learning: Adjust exit rules based on actual trade outcomes
        
        Args:
            exit_data: {
                "ticker": str,
                "tier": int,
                "entry_price": float,
                "exit_price": float,
                "exit_reason": str,
                "pnl_pct": float,
                "exit_type": str (profit/loss/chart/time),
                "days_held": int,
                "rsi_at_exit": float
            }
        """
        
        if not self.rules.get("learning_enabled"):
            return
        
        tier = exit_data["tier"]
        tier_name = f"tier{tier}"
        pnl_pct = exit_data["pnl_pct"]
        exit_type = exit_data["exit_type"]
        
        logger.info(f"🤖 LEARNING: {exit_data['ticker']} ({tier_name}) | Type: {exit_type} | P&L: {pnl_pct:+.2f}%")
        
        # Build learning history if doesn't exist
        if "learning_history" not in self.rules:
            self.rules["learning_history"] = {
                "tier1": [], "tier2": [], "tier3": []
            }
        
        self.rules["learning_history"][tier_name].append({
            "timestamp": datetime.now().isoformat(),
            "ticker": exit_data["ticker"],
            "exit_type": exit_type,
            "pnl_pct": pnl_pct,
            "days_held": exit_data["days_held"]
        })
        
        # After 10 trades per tier, optimize rules
        if len(self.rules["learning_history"][tier_name]) % 10 == 0:
            self._optimize_tier_rules(tier_name)
    
    def _optimize_tier_rules(self, tier_name):
        """
        Analyze last 10 trades and adjust rules for this tier
        Goal: Maximize win rate while holding winners longer
        """
        
        history = self.rules["learning_history"][tier_name]
        recent = history[-10:]
        
        # Calculate statistics
        profit_trades = [t for t in recent if t["pnl_pct"] > 0]
        loss_trades = [t for t in recent if t["pnl_pct"] < 0]
        
        win_rate = len(profit_trades) / len(recent) * 100 if recent else 0
        avg_win = sum(t["pnl_pct"] for t in profit_trades) / len(profit_trades) if profit_trades else 0
        avg_loss = abs(sum(t["pnl_pct"] for t in loss_trades) / len(loss_trades)) if loss_trades else 0
        
        logger.info(f"📊 {tier_name} stats (last 10): Win rate {win_rate:.0f}% | Avg win {avg_win:+.2f}% | Avg loss {avg_loss:+.2f}%")
        
        # Optimization logic
        current_rules = self.rules[tier_name]
        
        # If win rate < 50%, tighten stop loss (protect capital)
        if win_rate < 50 and avg_loss > 0:
            new_stop = current_rules["stop_loss_pct"] - 0.3
            current_rules["stop_loss_pct"] = max(1.0, new_stop)
            logger.info(f"  ↓ Stop loss tightened to {current_rules['stop_loss_pct']:.1f}% (low win rate)")
        
        # If win rate > 70%, slightly loosen stop loss (let winners run)
        elif win_rate > 70:
            new_stop = current_rules["stop_loss_pct"] + 0.2
            current_rules["stop_loss_pct"] = min(5.0, new_stop)
            logger.info(f"  ↑ Stop loss loosened to {current_rules['stop_loss_pct']:.1f}% (high win rate)")
        
        # If avg win is good, increase profit target slightly
        if avg_win > current_rules["profit_target_pct"] * 1.2:
            current_rules["profit_target_pct"] = min(avg_win * 0.9, 8.0)
            logger.info(f"  ↑ Profit target adjusted to {current_rules['profit_target_pct']:.1f}%")
        
        self.rules["learning_phase"] = "optimizing" if win_rate > 55 else "collecting"
        self.save_exit_rules()
    
    def get_summary(self):
        """Return current exit rules summary"""
        summary = "📋 CURRENT EXIT RULES:\n"
        for tier in ["tier1", "tier2", "tier3"]:
            rules = self.rules[tier]
            summary += f"\n{tier.upper()}:\n"
            summary += f"  Stop Loss: {rules['stop_loss_pct']}%\n"
            summary += f"  Profit Target: {rules['profit_target_pct']}%\n"
            summary += f"  Max Hold: {rules['max_hold_days']} days\n"
        return summary


if __name__ == "__main__":
    # Test the exit manager
    logging.basicConfig(level=logging.INFO)
    
    manager = ExitManager()
    
    # Test position 1: Should take profit
    exit1 = manager.evaluate_exit(
        position={"ticker": "TMPV", "buy_price": 397.2, "qty": 3, "tier": 3, "buy_date": "2026-06-04"},
        current_price=407.5,  # +2.6%
        rsi=72,
        sma20=400,
        sma50=395,
        days_held=2,
        previous_close=405
    )
    print(f"Test 1 - Should HOLD (target 2.5%): {exit1}")
    
    # Test position 2: Should cut loss
    exit2 = manager.evaluate_exit(
        position={"ticker": "JSLL", "buy_price": 702.85, "qty": 8, "tier": 2, "buy_date": "2026-06-04"},
        current_price=685,  # -2.5%
        rsi=35,
        sma20=695,
        sma50=710,
        days_held=1,
        previous_close=700
    )
    print(f"Test 2 - Should EXIT (stop loss): {exit2}")
    
    print("\n" + manager.get_summary())
