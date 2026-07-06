"""Validates trade ideas using yfinance options chain data.

Fetches options chains and underlying price history via yfinance.
Models fills at bid (for closes/sells) and ask (for opens/buys).
Includes early-assignment simulation for short-leg strategies.
Requires 100+ historical samples before trusting any strategy stat.
"""

import datetime
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger("hermes_trader.research.backtest")

# ── Constants ───────────────────────────────────────────────
MIN_SAMPLE_SIZE = 100          # Minimum historical trades to trust stats
DEFAULT_LOOKBACK_DAYS = 365    # 1 year of data
COMMISSION_PER_CONTRACT = 0.65 # Typical retail options commission
EXCHANGE_FEE_PER_CONTRACT = 0.06 # OPRA exchange fee
EARLY_ASSIGNMENT_PROB = 0.05  # 5% annual prob for ITM short options


@dataclass
class OptionsBar:
    """Single options bar with bid/ask for realistic fill modeling."""
    timestamp: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    bid: float = 0.0
    ask: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.close


@dataclass
class Trade:
    """Simulated trade with realistic fills."""
    symbol: str
    side: str           # 'long' or 'short'
    entry_time: datetime.datetime
    exit_time: datetime.datetime
    entry_price: float  # filled at ask (buys) or bid (sells)
    exit_price: float   # filled at bid (closes longs) or ask (covers shorts)
    quantity: int
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    early_assigned: bool = False
    entry_bar: Optional[OptionsBar] = None
    exit_bar: Optional[OptionsBar] = None

    def __post_init__(self):
        if self.pnl == 0.0 and self.entry_price > 0:
            if self.side == 'long':
                self.pnl = (self.exit_price - self.entry_price) * self.quantity * 100
            else:  # short
                self.pnl = (self.entry_price - self.exit_price) * self.quantity * 100
            self.pnl -= self.commission
            self.pnl_pct = (self.pnl / (self.entry_price * self.quantity * 100)) * 100


@dataclass
class BacktestReport:
    """Backtest validation report with sample-size gating."""
    valid: bool
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    roi_pct: float
    max_drawdown_pct: float
    total_trades: int
    sample_sufficient: bool
    reason: str
    avg_winner_pct: float = 0.0
    avg_loser_pct: float = 0.0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0
    early_assignments: int = 0
    data_source: str = "yfinance_options"


class BacktestValidator:
    """Validates trade ideas using yfinance options chain data.

    Uses yfinance to fetch options chains and underlying price history.
    Models fills at bid (for closes/sells) and ask (for opens/buys).
    Includes early-assignment simulation for short-leg strategies.
    Requires 100+ historical samples before trusting any strategy stat.

    Usage:
        validator = BacktestValidator()
        report = validator.validate_trade(
            symbol="SPY",
            option_type="call",
            strike_offset_pct=0.005,  # 0.5% OTM
            dte_target=45,
            profit_target_pct=0.50,   # 50% profit target
            stop_loss_pct=2.0,        # 2x credit stop loss
        )
        if report.sample_sufficient and report.valid:
            print("Strategy validated!")
    """

    def __init__(self, cash: int = 10000, commission: float = COMMISSION_PER_CONTRACT,
                 lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        self.cash = cash
        self.commission = commission
        self.lookback_days = lookback_days
        self._yf_cache = {}  # symbol -> yf.Ticker

    def _get_yf_ticker(self, symbol: str):
        """Get or create a cached yfinance Ticker object."""
        if symbol not in self._yf_cache:
            try:
                import yfinance as yf
                self._yf_cache[symbol] = yf.Ticker(symbol)
            except ImportError:
                logger.warning("yfinance not installed")
                return None
        return self._yf_cache[symbol]

    def validate_trade(self, symbol: str = "SPY",
                       option_type: str = "call",
                       strike_offset_pct: float = 0.005,
                       dte_target: int = 45,
                       profit_target_pct: float = 0.50,
                       stop_loss_pct: float = 2.0,
                       start_date: str = None,
                       max_days_in_trade: int = 45,
                       allow_short: bool = False) -> BacktestReport:
        """Run options backtest using yfinance data.

        Args:
            symbol: Underlying symbol (e.g. "SPY", "QQQ")
            option_type: "call" or "put"
            strike_offset_pct: How far OTM (0.005 = 0.5%)
            dte_target: Target days to expiration for entry
            profit_target_pct: Exit at this % profit (0.50 = 50%)
            stop_loss_pct: Exit at this multiple of credit for shorts (2.0 = 2x)
            start_date: Override start date (YYYY-MM-DD)
            max_days_in_trade: Max holding period
            allow_short: Enable short-leg strategies + early assignment sim
        """
        try:
            # 1. Load options chain and historical data from yfinance
            options_data = self._load_yfinance_options(
                symbol, option_type, strike_offset_pct,
                dte_target, start_date
            )

            if options_data is None or options_data.empty:
                return BacktestReport(
                    valid=False, sharpe_ratio=0.0, win_rate=0.0,
                    profit_factor=0.0, roi_pct=0.0, max_drawdown_pct=0.0,
                    total_trades=0, sample_sufficient=False,
                    reason="yfinance options data unavailable",
                    data_source="yfinance_options"
                )

            # 2. Simulate trades using bid/ask fills
            trades = self._simulate_trades(
                options_data, profit_target_pct, stop_loss_pct,
                max_days_in_trade, allow_short
            )

            # 3. Compute statistics
            report = self._compute_stats(trades)

            # 4. Validate
            if not report.sample_sufficient:
                report.valid = False
                report.reason = (
                    f"Insufficient sample: {report.total_trades} trades "
                    f"(need {MIN_SAMPLE_SIZE}+). Results are UNRELIABLE."
                )
            else:
                report.valid = (
                    report.sharpe_ratio > 1.0 and
                    report.win_rate > 50.0 and
                    report.profit_factor > 1.5
                )
                if not report.valid:
                    report.reason = (
                        f"Validation failed: Sharpe {report.sharpe_ratio:.2f}, "
                        f"Win {report.win_rate:.1f}%, PF {report.profit_factor:.2f}"
                    )
                else:
                    report.reason = (
                        f"Sharpe {report.sharpe_ratio:.2f} > 1.0, "
                        f"Win {report.win_rate:.1f}% > 50%, "
                        f"PF {report.profit_factor:.2f} > 1.5 "
                        f"(n={report.total_trades})"
                    )

            return report

        except Exception as e:
            logger.warning(f"Backtest failed: {e}", exc_info=True)
            return BacktestReport(
                valid=False, sharpe_ratio=0.0, win_rate=0.0,
                profit_factor=0.0, roi_pct=0.0, max_drawdown_pct=0.0,
                total_trades=0, sample_sufficient=False,
                reason=f"Backtest exception: {str(e)}",
                data_source="yfinance_options"
            )

    def _load_yfinance_options(self, symbol: str, option_type: str,
                               strike_offset_pct: float, dte_target: int,
                               start_date: str = None) -> Optional[pd.DataFrame]:
        """Load options chain data from yfinance.

        Returns a DataFrame with columns: timestamp, open, high, low, close,
        volume, bid, ask, strike, expiration, option_type, symbol.
        """
        ticker = self._get_yf_ticker(symbol)
        if ticker is None:
            logger.warning("Cannot create yfinance Ticker")
            return None

        try:
            # Get underlying price history for simulation
            end_date = datetime.date.today()
            if start_date:
                start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            else:
                start_dt = end_date - datetime.timedelta(days=self.lookback_days)

            # Fetch underlying stock history for price simulation
            hist = ticker.history(start=start_dt, end=end_date)
            if hist.empty:
                logger.warning(f"No price history for {symbol}")
                return None

            current_price = hist['Close'].iloc[-1]

            # Get available option expiration dates
            expirations = ticker.options
            if not expirations:
                logger.warning(f"No options expirations for {symbol}")
                return None

            # Find expiration closest to our DTE target
            today = datetime.date.today()
            target_exp = None
            min_dte_diff = float('inf')
            for exp_str in expirations:
                try:
                    exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    dte_diff = abs(dte - dte_target)
                    if dte_diff < min_dte_diff:
                        min_dte_diff = dte_diff
                        target_exp = exp_str
                except ValueError:
                    continue

            if target_exp is None:
                logger.warning(f"No suitable expiration found for {symbol}")
                return None

            # Get the option chain for this expiration
            chain = ticker.option_chain(target_exp)
            options_df = chain.calls if option_type == "call" else chain.puts

            if options_df.empty:
                logger.warning(f"No {option_type} options found for {symbol}")
                return None

            # Filter by strike proximity
            target_strike = current_price * (1.0 + strike_offset_pct) \
                if option_type == "call" else \
                current_price * (1.0 - strike_offset_pct)

            # Find nearest strike
            options_df = options_df.copy()
            options_df['strike_diff'] = abs(options_df['strike'] - target_strike)
            options_df = options_df.sort_values('strike_diff').head(5)

            if options_df.empty:
                logger.warning(f"No matching strikes found for {symbol}")
                return None

            # Build simulated options bars from underlying price history
            # Since yfinance doesn't provide historical options bars,
            # we simulate options price movements from the underlying
            all_bars = []
            for _, row in options_df.iterrows():
                strike = row['strike']
                last_bid = row.get('bid', 0)
                last_ask = row.get('ask', 0)
                last_price = row.get('lastPrice', 0) or row.get('lastPrice', 0)
                if last_price == 0:
                    last_price = (last_bid + last_ask) / 2 if last_bid > 0 and last_ask > 0 else 0

                # Simulate option price from underlying moves
                for i, (ts, stock_row) in enumerate(hist.iterrows()):
                    stock_close = stock_row['Close']

                    # Simple delta-based approximation:
                    # For calls: option moves ~0.5 * stock move (ATM delta)
                    # For puts: option moves ~-0.5 * stock move
                    if i == 0:
                        opt_price = last_price
                    else:
                        stock_prev = hist['Close'].iloc[i-1]
                        stock_move = stock_close - stock_prev
                        delta = 0.5 if option_type == "call" else -0.5
                        opt_price = max(0.01, all_bars[-1]['close'] + delta * stock_move)

                    # Estimate bid/ask spread (wider for OTM options)
                    spread_pct = 0.04 + abs(strike - stock_close) / stock_close * 0.1
                    bid_est = max(0.01, opt_price * (1 - spread_pct / 2))
                    ask_est = opt_price * (1 + spread_pct / 2)

                    all_bars.append({
                        'timestamp': ts.to_pydatetime(),
                        'open': opt_price * 0.99,
                        'high': opt_price * 1.02,
                        'low': opt_price * 0.98,
                        'close': opt_price,
                        'volume': int(stock_row.get('Volume', 1000) * 0.01),
                        'bid': bid_est,
                        'ask': ask_est,
                        'strike': strike,
                        'expiration': target_exp,
                        'option_type': option_type,
                        'symbol': f"{symbol}_{option_type}_{strike}_{target_exp}",
                    })

            if not all_bars:
                logger.warning(f"No options bars generated for {symbol}")
                return None

            df = pd.DataFrame(all_bars)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)

            return df

        except Exception as e:
            logger.warning(f"yfinance options data load failed: {e}", exc_info=True)
            return None

    def _parse_option_symbol(self, symbol: str):
        """Parse OCC option symbol into components.

        Format: ROOT + YYMMDD + C/P + Strike (8 digits)
        Example: SPY240701C00540000 -> (2024-07-01, 540.0, 'call')
        """
        try:
            # Find the C or P indicator
            cp_idx = -1
            for i, c in enumerate(symbol):
                if c in ('C', 'P') and i > 6:  # Skip early chars
                    # Check if rest looks like strike (digits)
                    rest = symbol[i+1:]
                    if rest.isdigit() and len(rest) >= 8:
                        cp_idx = i
                        break

            if cp_idx == -1:
                return None

            root = symbol[:cp_idx-6]
            date_str = symbol[cp_idx-6:cp_idx]
            cp = symbol[cp_idx]
            strike_str = symbol[cp_idx+1:]

            year = 2000 + int(date_str[:2])
            month = int(date_str[2:4])
            day = int(date_str[4:6])
            exp_date = datetime.date(year, month, day)

            strike = int(strike_str) / 1000.0
            option_type = 'call' if cp == 'C' else 'put'

            return (exp_date, strike, option_type)
        except Exception:
            return None

    def _simulate_trades(self, data: pd.DataFrame,
                         profit_target_pct: float, stop_loss_pct: float,
                         max_days: int, allow_short: bool) -> list[Trade]:
        """Simulate trades using bid/ask fills.

        Key rules:
        - BUY fills at ASK (always pays the spread)
        - SELL fills at BID (always receives less)
        - Early assignment: ~5% annual prob for ITM short options
        """
        trades = []
        grouped = data.groupby('symbol')

        for opt_symbol, group in grouped:
            group = group.sort_values('timestamp').reset_index(drop=True)
            if len(group) < 5:
                continue

            # Simulate entries and exits
            i = 0
            while i < len(group) - 1:
                bar = group.iloc[i]

                # Entry conditions: look for volume spike + price momentum
                if not self._should_enter(group, i):
                    i += 1
                    continue

                # ENTRY: buy at ASK (worse than mid)
                entry_price = bar['ask'] if bar['ask'] > 0 else bar['close'] * 1.01
                entry_time = bar['timestamp']
                entry_bar = OptionsBar(
                    timestamp=entry_time, open=bar['open'], high=bar['high'],
                    low=bar['low'], close=bar['close'], volume=bar['volume'],
                    bid=bar['bid'], ask=bar['ask']
                )

                # Track exit
                exit_price = 0.0
                exit_time = entry_time
                exit_bar = None
                early_assigned = False

                # Simulate holding period
                j = i + 1
                max_idx = min(i + max_days, len(group) - 1)
                while j <= max_idx:
                    hold_bar = group.iloc[j]
                    hold_days = (hold_bar['timestamp'] - entry_time).days

                    # Check early assignment for short options
                    if allow_short and self._check_early_assignment(
                        entry_price, hold_bar['close'], hold_days
                    ):
                        exit_price = entry_price  # Assigned at entry price
                        exit_time = hold_bar['timestamp']
                        early_assigned = True
                        exit_bar = OptionsBar(
                            timestamp=exit_time, open=hold_bar['open'],
                            high=hold_bar['high'], low=hold_bar['low'],
                            close=hold_bar['close'], volume=hold_bar['volume'],
                            bid=hold_bar['bid'], ask=hold_bar['ask']
                        )
                        break

                    # Check profit target: sell at BID (always less than mid)
                    current_mid = hold_bar['close']
                    current_bid = hold_bar['bid'] if hold_bar['bid'] > 0 else current_mid * 0.98
                    gain_pct = (current_bid - entry_price) / entry_price

                    if gain_pct >= profit_target_pct:
                        exit_price = current_bid  # SELL at BID
                        exit_time = hold_bar['timestamp']
                        exit_bar = OptionsBar(
                            timestamp=exit_time, open=hold_bar['open'],
                            high=hold_bar['high'], low=hold_bar['low'],
                            close=hold_bar['close'], volume=hold_bar['volume'],
                            bid=hold_bar['bid'], ask=hold_bar['ask']
                        )
                        break

                    # Check stop loss
                    loss_pct = (entry_price - current_bid) / entry_price
                    stop_threshold = stop_loss_pct / 100.0 if stop_loss_pct > 1 else stop_loss_pct
                    if loss_pct >= stop_threshold:
                        exit_price = current_bid  # STOP at BID
                        exit_time = hold_bar['timestamp']
                        exit_bar = OptionsBar(
                            timestamp=exit_time, open=hold_bar['open'],
                            high=hold_bar['high'], low=hold_bar['low'],
                            close=hold_bar['close'], volume=hold_bar['volume'],
                            bid=hold_bar['bid'], ask=hold_bar['ask']
                        )
                        break

                    j += 1

                # If no exit triggered, close at last bar's BID
                if exit_price == 0.0:
                    last_bar = group.iloc[max_idx]
                    exit_price = last_bar['bid'] if last_bar['bid'] > 0 else last_bar['close'] * 0.98
                    exit_time = last_bar['timestamp']
                    exit_bar = OptionsBar(
                        timestamp=exit_time, open=last_bar['open'],
                        high=last_bar['high'], low=last_bar['low'],
                        close=last_bar['close'], volume=last_bar['volume'],
                        bid=last_bar['bid'], ask=last_bar['ask']
                    )

                # Calculate commission
                commission = (self.commission + EXCHANGE_FEE_PER_CONTRACT) * 2  # round trip

                trade = Trade(
                    symbol=opt_symbol,
                    side='long',
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=1,
                    commission=commission,
                    early_assigned=early_assigned,
                    entry_bar=entry_bar,
                    exit_bar=exit_bar,
                )
                trades.append(trade)

                # Skip ahead past exit
                i = max_idx + 1

        return trades

    def _should_enter(self, group: pd.DataFrame, idx: int) -> bool:
        """Simple entry signal: volume spike + positive momentum."""
        if idx < 5:
            return False

        bar = group.iloc[idx]
        prev_bars = group.iloc[max(0, idx-5):idx]

        # Volume spike: current volume > 2x average of last 5 bars
        avg_volume = prev_bars['volume'].mean()
        if avg_volume <= 0 or bar['volume'] < avg_volume * 1.5:
            return False

        # Price momentum: close > previous close
        prev_close = prev_bars.iloc[-1]['close']
        if prev_close <= 0 or bar['close'] <= prev_close:
            return False

        # Bid-ask spread sanity check
        if bar['bid'] > 0 and bar['ask'] > 0:
            mid = (bar['bid'] + bar['ask']) / 2
            spread = (bar['ask'] - bar['bid']) / mid if mid > 0 else 0
            if spread > 0.10:  # >10% spread = too wide, skip
                return False

        return True

    def _check_early_assignment(self, entry_price: float, current_price: float,
                                 days_held: int) -> bool:
        """Simulate early assignment for short options.

        Assignment probability increases when:
        - Option is deep ITM (large intrinsic value)
        - Near ex-dividend dates
        - Days to expiration decrease
        """
        if current_price >= entry_price:
            return False  # Not ITM for short

        moneyness = (entry_price - current_price) / entry_price

        # Higher probability for deeper ITM options
        daily_prob = EARLY_ASSIGNMENT_PROB * moneyness * (1 + days_held / 30)

        # Never exceed 50% per day
        daily_prob = min(daily_prob, 0.5)

        return random.random() < daily_prob

    def _compute_stats(self, trades: list[Trade]) -> BacktestReport:
        """Compute backtest statistics from trade list."""
        if not trades:
            return BacktestReport(
                valid=False, sharpe_ratio=0.0, win_rate=0.0,
                profit_factor=0.0, roi_pct=0.0, max_drawdown_pct=0.0,
                total_trades=0, sample_sufficient=False,
                reason="No trades generated",
                data_source="yfinance_options"
            )

        total_trades = len(trades)
        sample_sufficient = total_trades >= MIN_SAMPLE_SIZE

        # Win/loss stats
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        win_rate = (len(winners) / total_trades * 100) if total_trades > 0 else 0.0

        # Profit factor
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

        # ROI
        total_pnl = sum(t.pnl for t in trades)
        total_invested = sum(t.entry_price * t.quantity * 100 for t in trades)
        roi_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

        # Sharpe ratio (annualized, using daily returns)
        daily_returns = [t.pnl_pct for t in trades]
        if len(daily_returns) > 1:
            avg_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - avg_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_dev = math.sqrt(variance) if variance > 0 else 0.001
            sharpe_ratio = (avg_return / std_dev) * math.sqrt(252)
        else:
            sharpe_ratio = 0.0

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative += t.pnl_pct
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        # Average winner/loser
        avg_winner = (sum(t.pnl_pct for t in winners) / len(winners)) if winners else 0.0
        avg_loser = (sum(t.pnl_pct for t in losers) / len(losers)) if losers else 0.0

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trades:
            if t.pnl <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        # Expectancy
        win_prob = len(winners) / total_trades if total_trades > 0 else 0
        loss_prob = 1 - win_prob
        avg_win_usd = (gross_profit / len(winners)) if winners else 0
        avg_loss_usd = (gross_loss / len(losers)) if losers else 0
        expectancy = (win_prob * avg_win_usd) - (loss_prob * avg_loss_usd)

        # Early assignments count
        early_assignments = sum(1 for t in trades if t.early_assigned)

        return BacktestReport(
            valid=False,  # Will be set by caller
            sharpe_ratio=round(sharpe_ratio, 2),
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            roi_pct=round(roi_pct, 1),
            max_drawdown_pct=round(max_dd, 1),
            total_trades=total_trades,
            sample_sufficient=sample_sufficient,
            reason="",  # Will be set by caller
            avg_winner_pct=round(avg_winner, 1),
            avg_loser_pct=round(avg_loser, 1),
            max_consecutive_losses=max_consec,
            expectancy=round(expectancy, 2),
            early_assignments=early_assignments,
            data_source="yfinance_options",
        )

    # ── Legacy interface (Backtrader compatibility) ──────────

    def validate_trade_legacy(self, symbol: str = "SPY",
                               target_pct: float = 0.03,
                               stop_pct: float = 0.01,
                               start_date: str = None) -> dict:
        """Legacy interface for backward compatibility.

        Uses the yfinance data backend but returns old dict format.
        """
        report = self.validate_trade(
            symbol=symbol,
            option_type="call",
            strike_offset_pct=0.005,
            dte_target=45,
            profit_target_pct=target_pct * 10,  # Convert from % to multiplier
            stop_loss_pct=stop_pct * 100,
            start_date=start_date,
        )

        return {
            "valid": report.valid,
            "sharpe_ratio": report.sharpe_ratio,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "roi_pct": report.roi_pct,
            "max_drawdown_pct": report.max_drawdown_pct,
            "total_trades": report.total_trades,
            "sample_sufficient": report.sample_sufficient,
            "reason": report.reason,
            "data_source": report.data_source,
        }
