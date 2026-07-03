"""Advanced position monitoring with trailing stops and partial profit-taking.

This module extends the basic position monitoring with:
- Trailing stop-loss that tightens as profits increase
- Partial profit-taking at predefined thresholds
- Time-based position decay
- Momentum-based position scaling
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..models.position_snapshot import PositionSnapshot, MarketSnapshot
from ..config import config

logger = logging.getLogger("hermes_trader.monitoring.advanced")


@dataclass
class TrailingStopConfig:
    """Configuration for trailing stop-loss."""
    initial_stop_pct: float = 0.02  # 2% initial stop
    trail_pct: float = 0.01  # 1% trailing distance
    activation_pct: float = 0.03  # Activate trailing after 3% profit
    
    def __post_init__(self):
        # Allow override from config
        if hasattr(config, 'trailing_stop_initial_pct'):
            self.initial_stop_pct = config.trailing_stop_initial_pct
        if hasattr(config, 'trailing_stop_trail_pct'):
            self.trail_pct = config.trailing_stop_trail_pct
        if hasattr(config, 'trailing_stop_activation_pct'):
            self.activation_pct = config.trailing_stop_activation_pct


@dataclass
class ProfitTakingConfig:
    """Configuration for partial profit-taking."""
    first_target_pct: float = 0.05  # Take 50% off at 5% profit
    first_take_pct: float = 0.50  # 50% of position
    second_target_pct: float = 0.10  # Take another 25% at 10% profit
    second_take_pct: float = 0.25  # 25% of position
    
    def __post_init__(self):
        if hasattr(config, 'profit_taking_first_target_pct'):
            self.first_target_pct = config.profit_taking_first_target_pct
        if hasattr(config, 'profit_taking_first_take_pct'):
            self.first_take_pct = config.profit_taking_first_take_pct


@dataclass
class TimeDecayConfig:
    """Configuration for time-based position decay.
    
    For 0DTE positions: use max_hold_hours=6 (must close same day).
    For swing positions: default 72h (3 days) max.
    """
    max_hold_hours: int = 72  # 3 days max for swings; 6 for 0DTE
    decay_start_hours: int = 48  # Start decaying after 48 hours
    decay_rate_per_hour: float = 0.005  # 0.5% per hour after start
    force_exit_time: str = "15:30"  # 3:30 PM ET — close all 0DTE before close


@dataclass
class PositionMonitor:
    """Advanced position monitor with trailing stops and profit-taking."""
    
    position: PositionSnapshot
    market: MarketSnapshot
    trailing_stop_config: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    profit_taking_config: ProfitTakingConfig = field(default_factory=ProfitTakingConfig)
    time_decay_config: TimeDecayConfig = field(default_factory=TimeDecayConfig)
    
    # Track state
    entry_price: float = 0.0
    peak_price: float = 0.0
    trailing_stop_price: Optional[float] = None
    profit_taken: bool = False
    partial_sell_executed: bool = False
    
    def __post_init__(self):
        self.entry_price = self.position.entry_price or self.position.avg_entry_price or 0.0
        self.peak_price = self.entry_price
        self.trailing_stop_price = None
        
    def update(self, market: MarketSnapshot) -> dict:
        """Update monitor with new market data and return actions.
        
        Returns dict with:
        - action: 'hold', 'sell_all', 'sell_partial'
        - sell_pct: percentage to sell (0-1)
        - reason: explanation string
        """
        self.market = market
        current_price = market.last_price
        
        # Update peak price
        if current_price > self.peak_price:
            self.peak_price = current_price
        
        # Check trailing stop
        stop_action = self._check_trailing_stop(current_price)
        if stop_action:
            return stop_action
        
        # Check profit-taking
        take_action = self._check_profit_taking(current_price)
        if take_action:
            return take_action
        
        # Check time decay
        decay_action = self._check_time_decay()
        if decay_action:
            return decay_action
        
        return {"action": "hold", "sell_pct": 0.0, "reason": "No exit conditions met"}
    
    def _check_trailing_stop(self, current_price: float) -> Optional[dict]:
        """Check if trailing stop has been hit."""
        if self.trailing_stop_price is None:
            # Initial stop: entry_price * (1 - initial_stop_pct)
            self.trailing_stop_price = self.entry_price * (1 - self.trailing_stop_config.initial_stop_pct)
            
            # Check if we should activate trailing (after reaching activation profit)
            profit_pct = (current_price - self.entry_price) / self.entry_price
            if profit_pct >= self.trailing_stop_config.activation_pct:
                # Activate trailing: stop follows peak
                self.trailing_stop_price = self.peak_price * (1 - self.trailing_stop_config.trail_pct)
        else:
            # Update trailing stop to follow peak
            potential_stop = self.peak_price * (1 - self.trailing_stop_config.trail_pct)
            if potential_stop > self.trailing_stop_price:
                self.trailing_stop_price = potential_stop
        
        # Check if stop hit
        if current_price <= self.trailing_stop_price:
            return {
                "action": "sell_all",
                "sell_pct": 1.0,
                "reason": f"Trailing stop hit at ${self.trailing_stop_price:.2f}"
            }
        
        return None
    
    def _check_profit_taking(self, current_price: float) -> Optional[dict]:
        """Check if profit-taking thresholds are reached."""
        if self.profit_taken:
            return None
            
        profit_pct = (current_price - self.entry_price) / self.entry_price
        
        # First target: take 50% off at 5% profit
        if not self.partial_sell_executed and profit_pct >= self.profit_taking_config.first_target_pct:
            self.partial_sell_executed = True
            return {
                "action": "sell_partial",
                "sell_pct": self.profit_taking_config.first_take_pct,
                "reason": f"First profit target hit: +{profit_pct*100:.1f}%"
            }
        
        # Second target: take another 25% at 10% profit
        if profit_pct >= self.profit_taking_config.second_target_pct:
            self.profit_taken = True
            return {
                "action": "sell_partial",
                "sell_pct": self.profit_taking_config.second_take_pct,
                "reason": f"Second profit target hit: +{profit_pct*100:.1f}%"
            }
        
        return None
    
    def _check_time_decay(self) -> Optional[dict]:
        """Check if position has exceeded max hold time."""
        if not self.position.entry_time:
            return None
            
        entry_time = datetime.fromisoformat(self.position.entry_time.replace('Z', '+00:00'))
        hold_hours = (datetime.utcnow() - entry_time).total_seconds() / 3600
        
        if hold_hours >= self.time_decay_config.max_hold_hours:
            return {
                "action": "sell_all",
                "sell_pct": 1.0,
                "reason": f"Max hold time exceeded ({hold_hours:.0f}h > {self.time_decay_config.max_hold_hours}h)"
            }
        
        # Decay warning (not a sell, but could be used for notifications)
        if hold_hours >= self.time_decay_config.decay_start_hours:
            decay_pct = (hold_hours - self.time_decay_config.decay_start_hours) * self.time_decay_config.decay_rate_per_hour
            if decay_pct >= 0.5:  # If decay exceeds 50%
                return {
                    "action": "sell_all",
                    "sell_pct": 1.0,
                    "reason": f"Time decay exceeded 50% ({decay_pct*100:.1f}%)"
                }
        
        return None


@dataclass
class MomentumScanner:
    """Scans for momentum-based trading opportunities.
    
    Detects when SPY breaks above 20-day high (momentum signal).
    """
    
    lookback_days: int = 20
    
    def check_spy_breakout(self) -> dict:
        """Check if SPY has broken above its 20-day high.
        
        Returns dict with:
        - in_breakout: bool
        - current_price: float
        - breakout_level: float (20-day high)
        - breakout_pct: float (percentage above breakout level)
        """
        try:
            import yfinance as yf
            import pandas as pd
            
            spy = yf.Ticker("SPY")
            hist = spy.history(period=f"{self.lookback_days}d")
            
            if hist.empty:
                return {"in_breakout": False, "current_price": 0.0, "breakout_level": 0.0, "breakout_pct": 0.0}
            
            current_price = hist['Close'].iloc[-1]
            breakout_level = hist['High'].max()
            
            if current_price > breakout_level:
                breakout_pct = (current_price - breakout_level) / breakout_level * 100
                return {
                    "in_breakout": True,
                    "current_price": current_price,
                    "breakout_level": breakout_level,
                    "breakout_pct": breakout_pct
                }
            
            return {
                "in_breakout": False,
                "current_price": current_price,
                "breakout_level": breakout_level,
                "breakout_pct": 0.0
            }
            
        except Exception as e:
            logger.warning(f"Momentum scan failed: {e}")
            return {"in_breakout": False, "current_price": 0.0, "breakout_level": 0.0, "breakout_pct": 0.0}


@dataclass  
class PyramidSizer:
    """Position sizing with pyramiding (adding to winning positions).
    
    Increases position size as trade moves in favorable direction.
    """
    
    base_notional: float = 500.0  # Base position size in USD
    pyramid_levels: int = 3  # Number of pyramid levels
    pyramid_increment_pct: float = 0.50  # 50% increase per level
    pyramid_trigger_pct: float = 0.02  # 2% profit to trigger next level
    max_total_notional: float = 5000.0  # Max total exposure
    
    def calculate_position_size(self, current_price: float, entry_price: float, 
                                  current_quantity: float = 0.0) -> dict:
        """Calculate position size for pyramiding.
        
        Args:
            current_price: Current market price
            entry_price: Original entry price
            current_quantity: Current position quantity
            
        Returns dict with:
        - add_notional: USD amount to add
        - add_quantity: shares to add
        - new_total_notional: total exposure after adding
        - level: current pyramid level (1-3)
        """
        profit_pct = (current_price - entry_price) / entry_price
        
        # Determine current level
        level = 1
        if profit_pct >= self.pyramid_trigger_pct * 2:
            level = 3
        elif profit_pct >= self.pyramid_trigger_pct:
            level = 2
        
        # Calculate increment
        increment = self.pyramid_increment_pct * (level - 1)
        position_size = self.base_notional * (1 + increment)
        
        # Check max exposure
        current_notional = current_quantity * current_price
        new_total = current_notional + position_size
        
        if new_total > self.max_total_notional:
            # Scale back to fit max
            remaining_capacity = self.max_total_notional - current_notional
            if remaining_capacity <= 0:
                return {
                    "add_notional": 0.0,
                    "add_quantity": 0.0,
                    "new_total_notional": current_notional,
                    "level": level,
                    "reason": "Max exposure reached"
                }
            position_size = remaining_capacity
        
        add_quantity = position_size / current_price
        
        return {
            "add_notional": position_size,
            "add_quantity": add_quantity,
            "new_total_notional": current_notional + position_size,
            "level": level,
            "reason": f"Pyramid level {level} at +{profit_pct*100:.1f}%"
        }
