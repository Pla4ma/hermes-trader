"""Indicators Configuration — exact parameters from research.

Based on 691 lines of 0DTE indicators research.
"""

# ═══════════════════════════════════════════════════════════════
# VWAP (Volume-Weighted Average Price) — #1 Institutional Signal
# ═══════════════════════════════════════════════════════════════
VWAP = {
    "chart_timeframe": "1min",
    "bands": {"1sigma": 1.0, "2sigma": 2.0},
    "entry_trigger": "touch_band_with_volume",
    "volume_spike": 1.5,  # 1.5x average 10-bar volume
    "stop": "below_2sigma_or_0.30_loss",
    "profit_target_1": "vwap_midpoint",
    "profit_target_2": "+1sigma_band",
    "win_rate": 0.68,
    "combined_win_rate": 0.72,  # With volume confirmation
}

# ═══════════════════════════════════════════════════════════════
# ORB (Opening Range Breakout) — Most Profitable 0DTE Strategy
# ═══════════════════════════════════════════════════════════════
ORB = {
    "timeframe": "15min",  # OPTIMAL for 0DTE
    "range_start": "9:30",
    "range_end": "9:45",
    "entry_time": "9:45-10:00",
    "min_range_height": 1.50,  # Skip if range < $1.50
    "max_range_height": 5.00,  # Skip if range > $5.00
    "volume_confirmation": 1.3,  # 1.3x first 15-min average
    "candle_confirmation": "close_above_range_high",
    "stop_loss": "below_range_low_or_0.30",
    "profit_target_1": "range_height_added",
    "profit_target_2": "2x_range_height",
    "time_stop": "3:30 PM",
    "win_rate": 0.72,
    "combined_win_rate": 0.78,  # With 5-min confirmation
}

# ═══════════════════════════════════════════════════════════════
# RSI — Fast Settings for 0DTE
# ═══════════════════════════════════════════════════════════════
RSI = {
    "1min": {"period": 5, "overbought": 80, "oversold": 20},
    "3min": {"period": 7, "overbought": 75, "oversold": 25},
    "5min": {"period": 9, "overbought": 70, "oversold": 30},
    "15min": {"period": 14, "overbought": 70, "oversold": 30},
    "best_timeframe": "1min",
    "best_period": 5,
    "divergence_win_rate": 0.70,
    "combined_vwap_win_rate": 0.75,
}

# ═══════════════════════════════════════════════════════════════
# MACD — Compressed for 0DTE
# ═══════════════════════════════════════════════════════════════
MACD = {
    "1min": {"fast": 3, "slow": 8, "signal": 3},
    "3min": {"fast": 5, "slow": 13, "signal": 5},
    "5min": {"fast": 8, "slow": 21, "signal": 5},
    "15min": {"fast": 12, "slow": 26, "signal": 9},
    "best_timeframe": "1min",
    "histogram_divergence_win_rate": 0.76,
    "combined_rsi_win_rate": 0.79,
}

# ═══════════════════════════════════════════════════════════════
# Volume Profile — Institutional Positioning
# ═══════════════════════════════════════════════════════════════
VOLUME_PROFILE = {
    "poc": "point_of_control",
    "vah": "value_area_high",
    "val": "value_area_low",
    "hvn": "high_volume_node",
    "lvn": "low_volume_node",
    "reversal_win_rate": 0.67,
    "combined_rsi_win_rate": 0.75,
}

# ═══════════════════════════════════════════════════════════════
# STRIKE SELECTION — Exact Parameters for $50 Account
# ═══════════════════════════════════════════════════════════════
STRIKE = {
    "delta_range": [0.15, 0.25],  # 15-25 delta (1-2% OTM)
    "otm_range_pct": [0.2, 0.5],  # 0.2-0.5% OTM
    "min_oi": 5000,
    "min_volume": 1000,
    "max_spread_pct": 0.10,  # Bid-ask < 10% of mid
}

# ═══════════════════════════════════════════════════════════════
# EXIT RULES — Exact Parameters
# ═══════════════════════════════════════════════════════════════
EXIT = {
    "profit_target_1": 0.50,  # 50% profit → sell 50%
    "profit_target_2": 1.00,  # 100% profit → sell 25%
    "profit_target_3": 2.00,  # 200% profit → let run or sell
    "stop_loss": 0.30,  # -30% of premium (NON-NEGOTIABLE)
    "dollar_stop": 15,  # Max $15 loss per trade
    "time_exit_morning": "11:00 AM",
    "time_exit_afternoon": "3:00 PM",
    "time_exit_absolute": "3:50 PM",
    "vwap_stop": "cross_VWAP_against_position",
    "rsi_stop": "rsi_extreme_reversal",
    "orb_stop": "fall_back_into_opening_range",
}

# ═══════════════════════════════════════════════════════════════
# RISK MANAGEMENT — $50 Account Rules
# ═══════════════════════════════════════════════════════════════
RISK = {
    "max_risk_per_trade": 0.20,  # 20% of account ($10)
    "max_position_size": 2,  # 2 contracts max
    "daily_loss_limit": 0.30,  # 30% of account ($15)
    "weekly_loss_limit": 0.50,  # 50% of account ($25)
    "max_trades_per_day": 3,
    "consecutive_loss_rules": {
        1: "continue_normal",
        2: "reduce_size_50%",
        3: "stop_for_day",
        4: "stop_for_week",
        5: "review_strategy_paper_trade_2_weeks",
    },
}

# ═══════════════════════════════════════════════════════════════
# SIGNAL CONFLUENCE — Minimum Requirements
# ═══════════════════════════════════════════════════════════════
CONFLUENCE = {
    "min_signals": 3,  # Minimum 3 of 5 indicators aligned
    "required_volume": True,  # Volume confirmation on every trade
    "avoid_conflicts": True,  # No conflicting signals
    "signals": ["VWAP", "ORB", "RSI", "MACD", "Volume"],
}
