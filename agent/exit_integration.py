"""
Integration: Exit Manager with Bot
Hooks exit_manager.py into position management cycle
"""

import json
import logging
from datetime import datetime
from pathlib import Path

try:
    from .exit_manager import ExitManager
    from .settings import log
except ImportError:
    from exit_manager import ExitManager
    import logging
    log = logging.getLogger(__name__)


class ExitIntegration:
    """Manages exit decisions for all positions in portfolio"""
    
    def __init__(self):
        self.exit_manager = ExitManager()
        self.portfolio_file = Path("portfolio_state_v4.json")
        log.info("✅ Exit Integration initialized")
    
    def check_all_positions(self, positions_data):
        """
        Check all positions for exit signals
        
        Args:
            positions_data: dict with positions and market data
        
        Returns:
            list of positions that should be exited
        """
        
        exits_triggered = []
        
        for position in positions_data.get("positions", []):
            ticker = position.get("ticker")
            
            # Get current market data (would come from MarketData API in real bot)
            market_data = self._get_market_data(ticker)
            
            if not market_data:
                continue
            
            # Evaluate exit for this position
            exit_signal = self.exit_manager.evaluate_exit(
                position=position,
                current_price=market_data.get("current_price"),
                rsi=market_data.get("rsi", 50),
                sma20=market_data.get("sma20"),
                sma50=market_data.get("sma50"),
                days_held=self._calculate_days_held(position.get("buy_date")),
                previous_close=market_data.get("previous_close"),
                market_regime=positions_data.get("regime", "NEUTRAL")
            )
            
            if exit_signal["should_exit"]:
                exits_triggered.append({
                    "ticker": ticker,
                    "exit_signal": exit_signal,
                    "position": position,
                    "market_data": market_data
                })
                
                log.warning(f"🔴 EXIT SIGNAL: {ticker} | {exit_signal['reason']}")
        
        return exits_triggered
    
    def _get_market_data(self, ticker):
        """Get current market data for ticker"""
        # This would integrate with your market data provider
        # For now, returns None (data would come from MarketData in bot)
        return None
    
    def _calculate_days_held(self, buy_date):
        """Calculate days held from buy_date"""
        if not buy_date:
            return 0
        try:
            buy_dt = datetime.fromisoformat(buy_date)
            return (datetime.now() - buy_dt).days
        except:
            return 0
    
    def record_exit(self, exit_data, trade_ledger=None):
        """
        Record exit trade for AI learning
        
        Args:
            exit_data: dict with exit signal and position details
            trade_ledger: TradeLedger instance (optional)
        """
        
        position = exit_data["position"]
        exit_signal = exit_data["exit_signal"]
        market_data = exit_data["market_data"]
        
        exit_pnl = exit_signal["expected_pnl_pct"]
        
        # Record in ledger if available
        if trade_ledger:
            trade_ledger.record(
                event="EXIT",
                ticker=position["ticker"],
                side="SELL",
                qty=position.get("qty", 0),
                price=market_data.get("current_price", 0),
                value=position.get("market_value", 0),
                pnl=exit_pnl,
                reason=exit_signal["reason"],
                strategy=f"exit_{exit_signal['type']}"
            )
        
        # Feed to learning engine
        learning_data = {
            "ticker": position["ticker"],
            "tier": position.get("tier", 3),
            "entry_price": position.get("buy_price", 0),
            "exit_price": market_data.get("current_price", 0),
            "exit_reason": exit_signal["reason"],
            "pnl_pct": exit_pnl,
            "exit_type": exit_signal["type"],
            "days_held": self._calculate_days_held(position.get("buy_date")),
            "rsi_at_exit": market_data.get("rsi", 50),
            "timestamp": datetime.now().isoformat()
        }
        
        # AI learns from this trade
        self.exit_manager.learn_from_trade(learning_data)
        
        log.info(f"✅ Exit recorded: {position['ticker']} | {exit_signal['type']} | P&L: {exit_pnl:+.2f}%")
    
    def get_current_rules(self):
        """Return current exit rules for display"""
        return self.exit_manager.get_summary()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    integrator = ExitIntegration()
    
    # Example: Test with sample position data
    test_positions = {
        "regime": "NEUTRAL",
        "positions": [
            {
                "ticker": "INFY",
                "tier": 3,
                "qty": 1,
                "buy_price": 1205.30,
                "market_value": 1204.10,
                "pnl_pct": -0.1,
                "buy_date": "2026-06-04"
            }
        ]
    }
    
    # Would check exits if market data was available
    print(integrator.get_current_rules())
