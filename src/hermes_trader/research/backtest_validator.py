"""Validates trade ideas by running backtests.
    
Uses Backtrader to simulate the trade strategy and check:
- Sharpe ratio > 1.0
- Win rate > 50%
- Profit factor > 1.5
"""

import datetime
import backtrader as bt
import backtrader.feeds as btfeeds
import pandas as pd
import yfinance as yf
import logging

logger = logging.getLogger("hermes_trader.research.backtest")


class BacktestValidator:
    """Validates trade ideas by running backtests.
    
    Uses Backtrader to simulate the trade strategy and check:
    - Sharpe ratio > 1.0
    - Win rate > 50%
    - Profit factor > 1.5
    """
    
    def __init__(self, cash: int = 10000, commission: float = 0.001, 
                 lookback_days: int = 365):
        self.cash = cash
        self.commission = commission
        self.lookback_days = lookback_days
    
    def validate_trade(self, symbol: str = "SPY", 
                       target_pct: float = 0.03,  # 3% profit target
                       stop_pct: float = 0.01,     # 1% stop loss
                       start_date: str = None) -> dict:
        """Run backtest and return validation results."""
        try:
            # Load market data
            data = self._load_market_data(symbol, start_date)
            if data is None:
                return {"valid": False, "reason": "Data unavailable"}
            
            cerebro = bt.Cerebro()
            cerebro.broker.setcash(self.cash)
            cerebro.broker.setcommission(commission=self.commission)
            
            # Add strategy
            cerebro.addstrategy(
                BacktestStrategy,
                target_pct=target_pct,
                stop_pct=stop_pct
            )
            
            # Add data
            data_feed = btfeeds.PandasData(dataname=data)
            cerebro.adddata(data_feed)
            
            # Add analyzers
            cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharperatio', riskfreerate=0.0)
            cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
            # Optional: drawdown analyzer if we want to report it
            cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
            
            # Run
            results = cerebro.run()
            strat = results[0]
            
            # Extract analysis
            sharpe_ratio = strat.analyzers.sharperatio.get_analysis().get('sharperatio', 0.0)
            # If Sharpe ratio is None (e.g., no variance), set to 0.0
            if sharpe_ratio is None:
                sharpe_ratio = 0.0
            
            ta = strat.analyzers.trades.get_analysis()
            total_trades = ta.get('total', {}).get('total', 0)
            if total_trades == 0:
                win_rate = 0.0
                profit_factor = 0.0
            else:
                won = ta.get('won', {}).get('total', 0)
                lost = ta.get('lost', {}).get('total', 0)
                win_rate = (won / total_trades) * 100.0 if total_trades > 0 else 0.0
                # Profit factor: gross profit / gross loss
                gross_profit = ta.get('won', {}).get('pnl', {}).get('total', 0.0)
                gross_loss = abs(ta.get('lost', {}).get('pnl', {}).get('total', 0.0))
                profit_factor = (gross_profit / gross_loss) if gross_loss != 0 else 0.0
            
            # Calculate ROI
            final_value = cerebro.broker.getvalue()
            roi = (final_value - self.cash) / self.cash * 100.0
            
            # Max drawdown (optional, for reporting)
            dd = strat.analyzers.drawdown.get_analysis()
            max_drawdown = dd.get('max', {}).get('drawdown', 0.0)
            
            # Validate
            valid = (
                sharpe_ratio > 1.0 and
                win_rate > 50.0 and
                profit_factor > 1.5
            )
            
            return {
                "valid": valid,
                "sharpe_ratio": round(sharpe_ratio, 2),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
                "roi_pct": round(roi, 1),
                "max_drawdown_pct": round(max_drawdown, 1),
                "reason": (
                    "Sharpe > 1.0, Win rate > 50%, Profit factor > 1.5" if valid 
                    else f"Validation failed: Sharpe {sharpe_ratio:.2f}, Win {win_rate:.1f}%, PF {profit_factor:.2f}"
                )
            }
            
        except Exception as e:
            logger.warning(f"Backtest failed: {e}", exc_info=True)
            return {"valid": False, "reason": f"Backtest exception: {str(e)}"}
    
    def _load_market_data(self, symbol: str, start_date: str) -> pd.DataFrame:
        """Load historical market data."""
        try:
            if start_date is None:
                # Default to lookback_days
                end = datetime.datetime.now()
                start = end - datetime.timedelta(days=self.lookback_days)
                start_date = start.strftime('%Y-%m-%d')
            
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=datetime.datetime.now().strftime('%Y-%m-%d'))
            
            if df.empty:
                return None
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            df.index = pd.to_datetime(df.index)
            return df
            
        except Exception as e:
            logger.warning(f"Data load failed for {symbol}: {e}")
            return None


class BacktestStrategy(bt.Strategy):
    """Simple long strategy with profit target and stop loss."""
    
    params = (
        ('target_pct', 0.03),  # 3% profit target
        ('stop_pct', 0.01),     # 1% stop loss
    )
    
    def __init__(self):
        self.order = None
        self.buy_price = None
        self.buy_comm = None
        
    def notify_order(self, order):
            if order.status in [order.Submitted, order.Accepted]:
                return

            if order.status in [order.Completed]:
                if order.isbuy():
                    self.buy_price = order.executed.price
                    # commission may not be available; set to 0
                    self.buy_comm = 0.0
                else:  # sell
                    pass
            elif order.status in [order.Canceled, order.Margin, order.Rejected]:
                pass

            self.order = None
    
    def notify_trade(self, trade):
        if not trade.isclosed:
            return
    
    def next(self):
        if self.order:
            return
        
        if not self.position:
            # Enter long with bracket order (profit target and stop loss)
            price = self.data.close[0]
            self.order = self.buy_bracket(
                price=price,
                stopprice=price * (1.0 - self.p.stop_pct),
                limitprice=price * (1.0 + self.p.target_pct)
            )
        else:
            # Already in position, wait for exit
            pass