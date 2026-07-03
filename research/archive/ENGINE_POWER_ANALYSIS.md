# 🔬 Options Engine Power Analysis — July 3, 2026

## Engine Power Level: 57% → Target: 100%

### What We Have (16 features):
1. VIX Term Structure (contango/backwardation)
2. 16 Delta Credit Spreads (tastylive sweet spot)
3. Iron Condors (both-side premium selling)
4. Day-of-Week Filter (avoid Friday, prefer Wednesday)
5. Kelly Sizing (10% fraction)
6. IV Rank Check (sell when IV > 50%)
7. 50% Profit Target (tastylive #1 rule)
8. 21 DTE Exit (close before gamma risk)
9. Market Regime (BULL/BEAR x HIGH/LOW VOL)
10. Vibe-Trading Research (market analysis)
11. TradingAgents Committee (bull/bear debate)
12. Trailing Stops (dynamic stop losses)
13. Risk Dashboard (real-time risk metrics)
14. Auto-Trader (scan + execute automatically)
15. Backtest Engine (strategy validation)
16. Options Analytics MCP (Black-Scholes Greeks)

### What We Need (12 features):
1. GEX Levels (gamma exposure for strike selection)
2. Options Flow (unusual volume detection)
3. IV Surface (full volatility surface)
4. 2nd/3rd Order Greeks (vanna, charm, vomma)
5. Max Pain (pin risk calculation)
6. Earnings Calendar (earnings integration)
7. Real-time P&L (per-strategy tracking)
8. Backtest with Real Data (real bid/ask backtesting)
9. Multi-leg Spreads (iron butterflies, condors)
10. Intraday Signals (momentum for options timing)
11. Correlation Analysis (underlying-options correlation)
12. Position Adjustments (rolls, closes, hedges)

### Research Findings:

#### VIX Term Structure (THE #1 EDGE):
- Contango (ratio >= 1.05): 84.2% WR, Sharpe 12.61
- Strong Contango (ratio >= 1.10): 86.2% WR, Sharpe 13.65
- Neutral: 71.8% WR, Sharpe 4.2
- Backwardation (ratio < 1.00): 41.6% WR, Sharpe -1.02
- Trading ONLY in contango eliminates 15% of days that cause nearly ALL losses

#### GEX (Gamma Exposure):
- Positive GEX = Dealers long gamma = price PINNING
- Negative GEX = Dealers short gamma = price AMPLIFYING
- GEX Flip Point = Strike where cumulative GEX changes sign
- Price tends to oscillate around flip point

#### Kelly Criterion:
- Full Kelly: 56% of account per trade (TOO AGGRESSIVE)
- Quarter Kelly: 14% = safe
- Tenth Kelly: 5.6% = very safe
- Recommendation: Use 10% Kelly (fractional Kelly)

#### Risk of Ruin:
- Edge: 12% per trade (78% WR, 0.33 reward/risk)
- At $5 risk per trade: 12.5% ruin after 10 trades
- At $2.50 risk per trade: 3.1% ruin after 10 trades
- KEY: Small position sizing = near-zero ruin

#### Greeks Reference:
- Delta: Rate of change of option price vs underlying
- Gamma: Rate of change of Delta vs underlying
- Theta: Time decay per day
- Vega: Sensitivity to 1% IV change
- Vanna: dDelta/dVol — how delta changes with IV
- Charm: dDelta/dTime — how delta changes with time
- Vomma: dGamma/dVol — how gamma changes with IV

#### Options Flow:
- Unusual Volume: Volume > 3x average daily volume
- Dark Pool Prints: Large block trades off-exchange
- Put/Call Ratio: PCR > 1.5 = bearish (contrarian bullish)
- Flow Divergence: Flow bullish + momentum negative = accumulation

#### Position Management:
- HOLD: Position neutral to positive, DTE > 21
- ROLL: Position tested, can roll for credit, DTE > 7
- CLOSE: 50% profit hit, loss exceeds 2x credit, DTE < 7
- CONVERT: Cannot roll for credit, reduce risk

#### 0DTE Strategies:
- Iron Condor: 70-75% WR, sell ATM ± 16 delta
- Credit Spread: 60-65% WR, sell OTM after first 15-min candle
- Butterfly: 50-55% WR but 3:1+ risk/reward
- GEX Scalp: 65% WR, trade at gamma flip points
- Theta Harvesting: 72% WR, sell 16 delta strangles

#### Strategy Selection Decision Tree:
1. Check VIX Term Structure → Sell premium if contango
2. Check VIX Level → High vol = sell iron condors, low vol = buy straddles
3. Check Market Regime → Bullish = sell puts, bearish = sell calls
4. Check GEX → Positive = sell strangles, negative = buy straddles
5. Check Put/Call Ratio → High PCR = contrarian bullish
6. Check Day of Week → Tuesday-Thursday optimal
7. Check Earnings → Avoid if earnings this week
8. Select Strategy → Based on all above
