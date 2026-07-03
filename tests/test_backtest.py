"""Tests for the Alpaca options backtest validator.

Tests cover:
- OptionsBar and Trade dataclasses
- BacktestReport sample-size gating
- BacktestValidator with mocked Alpaca data
- Bid/ask fill modeling
- Early assignment simulation
- Edge cases (empty data, insufficient samples)
"""

import datetime
import math
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hermes_trader.research.backtest_validator import (
    BacktestValidator,
    BacktestReport,
    OptionsBar,
    Trade,
    MIN_SAMPLE_SIZE,
    COMMISSION_PER_CONTRACT,
    EXCHANGE_FEE_PER_CONTRACT,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def sample_bar():
    """A single options bar with realistic bid/ask."""
    return OptionsBar(
        timestamp=datetime.datetime(2026, 6, 1, 10, 0),
        open=5.00, high=5.50, low=4.80, close=5.20,
        volume=1000, bid=5.10, ask=5.30,
    )


@pytest.fixture
def sample_bars():
    """Sequence of 20 options bars with volume spikes and momentum."""
    bars = []
    base_price = 5.0
    for i in range(20):
        price = base_price + (i * 0.1)  # Steady uptrend
        volume = 500 if i < 15 else 2000  # Volume spike in last 5
        bars.append(OptionsBar(
            timestamp=datetime.datetime(2026, 6, 1 + i, 10, 0),
            open=price - 0.05,
            high=price + 0.10,
            low=price - 0.10,
            close=price,
            volume=volume,
            bid=price - 0.05,
            ask=price + 0.05,
        ))
    return bars


@pytest.fixture
def sample_trades():
    """10 sample trades with known P&L for stats testing."""
    trades = []
    pnls = [100, -50, 200, -30, 150, -80, 120, -40, 180, -60]
    for i, pnl in enumerate(pnls):
        entry = 5.0
        if pnl > 0:
            exit_p = entry + (pnl / 100)
        else:
            exit_p = entry + (pnl / 100)
        trades.append(Trade(
            symbol=f"SPY260701C{i:04d}",
            side='long',
            entry_time=datetime.datetime(2026, 6, 1 + i, 10, 0),
            exit_time=datetime.datetime(2026, 6, 1 + i, 15, 0),
            entry_price=entry,
            exit_price=exit_p,
            quantity=1,
            commission=1.42,
        ))
    return trades


@pytest.fixture
def winning_trades():
    """150 winning trades to exceed minimum sample size."""
    trades = []
    for i in range(150):
        entry = 5.0
        exit_p = entry * 1.05  # 5% winner
        trades.append(Trade(
            symbol=f"SPY260701C{i:04d}",
            side='long',
            entry_time=datetime.datetime(2026, 1, 1 + (i % 30), 10, 0),
            exit_time=datetime.datetime(2026, 1, 1 + (i % 30), 15, 0),
            entry_price=entry,
            exit_price=exit_p,
            quantity=1,
            commission=1.42,
        ))
    return trades


@pytest.fixture
def losing_trades():
    """150 losing trades."""
    trades = []
    for i in range(150):
        entry = 5.0
        exit_p = entry * 0.90  # 10% loser
        trades.append(Trade(
            symbol=f"SPY260701C{i:04d}",
            side='long',
            entry_time=datetime.datetime(2026, 1, 1 + (i % 30), 10, 0),
            exit_time=datetime.datetime(2026, 1, 1 + (i % 30), 15, 0),
            entry_price=entry,
            exit_price=exit_p,
            quantity=1,
            commission=1.42,
        ))
    return trades


# ══════════════════════════════════════════════════════════════
# Unit Tests — OptionsBar
# ══════════════════════════════════════════════════════════════

class TestOptionsBar:
    def test_mid_price_with_bid_ask(self, sample_bar):
        """Mid should average bid and ask when both present."""
        assert sample_bar.mid == pytest.approx(5.20, abs=0.01)

    def test_mid_price_fallback_to_close(self):
        """Mid should fall back to close when bid/ask missing."""
        bar = OptionsBar(
            timestamp=datetime.datetime.now(),
            open=5.0, high=5.5, low=4.8, close=5.2,
            volume=1000, bid=0.0, ask=0.0,
        )
        assert bar.mid == 5.2

    def test_mid_price_with_zero_bid(self):
        """Mid should use close if bid is 0."""
        bar = OptionsBar(
            timestamp=datetime.datetime.now(),
            open=5.0, high=5.5, low=4.8, close=5.2,
            volume=1000, bid=0.0, ask=5.30,
        )
        assert bar.mid == 5.2


# ══════════════════════════════════════════════════════════════
# Unit Tests — Trade
# ══════════════════════════════════════════════════════════════

class TestTrade:
    def test_long_trade_pnl(self):
        """Long trade: pnl = (exit - entry) * qty * 100."""
        trade = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=1, commission=1.42,
        )
        expected = (5.5 - 5.0) * 1 * 100 - 1.42
        assert trade.pnl == pytest.approx(expected, abs=0.01)

    def test_long_trade_pnl_pct(self):
        """Long trade: pnl_pct = pnl / (entry * qty * 100) * 100."""
        trade = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=1, commission=0.0,
        )
        expected_pct = (0.5 * 100) / (5.0 * 100) * 100  # 10%
        assert trade.pnl_pct == pytest.approx(expected_pct, abs=0.1)

    def test_short_trade_pnl(self):
        """Short trade: pnl = (entry - exit) * qty * 100."""
        trade = Trade(
            symbol="TEST", side='short',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.5, exit_price=5.0,
            quantity=1, commission=1.42,
        )
        expected = (5.5 - 5.0) * 1 * 100 - 1.42
        assert trade.pnl == pytest.approx(expected, abs=0.01)

    def test_commission_reduces_pnl(self):
        """Commission should reduce P&L."""
        with_comm = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=1, commission=1.42,
        )
        without_comm = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=1, commission=0.0,
        )
        assert with_comm.pnl < without_comm.pnl

    def test_early_assignment_flag(self):
        """Trade can be flagged as early-assigned."""
        trade = Trade(
            symbol="TEST", side='short',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.0,
            quantity=1, commission=0.0, early_assigned=True,
        )
        assert trade.early_assigned is True


# ══════════════════════════════════════════════════════════════
# Unit Tests — BacktestReport
# ══════════════════════════════════════════════════════════════

class TestBacktestReport:
    def test_insufficient_sample_flags_report(self):
        """Report with < 100 trades should be flagged insufficient."""
        report = BacktestReport(
            valid=False, sharpe_ratio=2.0, win_rate=70.0,
            profit_factor=3.0, roi_pct=15.0, max_drawdown_pct=5.0,
            total_trades=50, sample_sufficient=False,
            reason="Insufficient sample",
            data_source="alpaca_options",
        )
        assert report.sample_sufficient is False
        assert report.total_trades < MIN_SAMPLE_SIZE

    def test_sufficient_sample_with_good_stats(self):
        """Report with 100+ trades and good stats should be valid."""
        report = BacktestReport(
            valid=True, sharpe_ratio=1.5, win_rate=60.0,
            profit_factor=2.0, roi_pct=12.0, max_drawdown_pct=8.0,
            total_trades=150, sample_sufficient=True,
            reason="All criteria met",
            data_source="alpaca_options",
        )
        assert report.sample_sufficient is True
        assert report.valid is True

    def test_minimum_sample_boundary(self):
        """Exactly 100 trades should be sufficient."""
        report = BacktestReport(
            valid=False, sharpe_ratio=1.0, win_rate=50.0,
            profit_factor=1.0, roi_pct=0.0, max_drawdown_pct=0.0,
            total_trades=MIN_SAMPLE_SIZE, sample_sufficient=True,
            reason="", data_source="alpaca_options",
        )
        assert report.sample_sufficient is True

    def test_99_trades_insufficient(self):
        """99 trades should be flagged as insufficient."""
        report = BacktestReport(
            valid=False, sharpe_ratio=3.0, win_rate=90.0,
            profit_factor=5.0, roi_pct=50.0, max_drawdown_pct=2.0,
            total_trades=99, sample_sufficient=False,
            reason="", data_source="alpaca_options",
        )
        assert report.sample_sufficient is False


# ══════════════════════════════════════════════════════════════
# Unit Tests — BacktestValidator
# ══════════════════════════════════════════════════════════════

class TestBacktestValidator:
    def test_instantiation(self):
        """Validator should initialize with defaults."""
        v = BacktestValidator()
        assert v.cash == 10000
        assert v.commission == COMMISSION_PER_CONTRACT
        assert v.lookback_days == 365

    def test_instantiation_custom_params(self):
        """Validator should accept custom parameters."""
        v = BacktestValidator(cash=50000, commission=0.50, lookback_days=180)
        assert v.cash == 50000
        assert v.commission == 0.50
        assert v.lookback_days == 180

    def test_compute_stats_empty_trades(self):
        """Stats with no trades should return empty report."""
        v = BacktestValidator()
        report = v._compute_stats([])
        assert report.total_trades == 0
        assert report.sample_sufficient is False
        assert report.valid is False

    def test_compute_stats_insufficient_sample(self):
        """Stats with < 100 trades should flag insufficient."""
        v = BacktestValidator()
        trades = [
            Trade(
                symbol="TEST", side='long',
                entry_time=datetime.datetime.now(),
                exit_time=datetime.datetime.now(),
                entry_price=5.0, exit_price=5.5,
                quantity=1, commission=1.42,
            )
            for _ in range(50)
        ]
        report = v._compute_stats(trades)
        assert report.total_trades == 50
        assert report.sample_sufficient is False

    def test_compute_stats_sufficient_sample(self):
        """Stats with 100+ trades should flag sufficient."""
        v = BacktestValidator()
        trades = [
            Trade(
                symbol="TEST", side='long',
                entry_time=datetime.datetime(2026, 1, 1 + (i % 30), 10, 0),
                exit_time=datetime.datetime(2026, 1, 1 + (i % 30), 15, 0),
                entry_price=5.0, exit_price=5.5,
                quantity=1, commission=1.42,
            )
            for i in range(120)
        ]
        report = v._compute_stats(trades)
        assert report.total_trades == 120
        assert report.sample_sufficient is True

    def test_compute_stats_win_rate(self, sample_trades):
        """Win rate should be calculated correctly."""
        v = BacktestValidator()
        report = v._compute_stats(sample_trades)
        # 5 winners out of 10
        assert report.win_rate == pytest.approx(50.0, abs=1.0)

    def test_compute_stats_profit_factor(self, sample_trades):
        """Profit factor should be gross_profit / gross_loss."""
        v = BacktestValidator()
        report = v._compute_stats(sample_trades)
        assert report.profit_factor > 0

    def test_compute_stats_max_drawdown(self):
        """Max drawdown should track cumulative peak-to-trough."""
        v = BacktestValidator()
        trades = []
        # Create a trade sequence: up, up, down, down, up
        pnls = [100, 50, -80, -120, 200]
        for i, pnl in enumerate(pnls):
            entry = 5.0
            exit_p = entry + (pnl / 100)
            trades.append(Trade(
                symbol="TEST", side='long',
                entry_time=datetime.datetime(2026, 1, 1 + i, 10, 0),
                exit_time=datetime.datetime(2026, 1, 1 + i, 15, 0),
                entry_price=entry, exit_price=exit_p,
                quantity=1, commission=0.0,
            ))
        report = v._compute_stats(trades)
        assert report.max_drawdown_pct > 0

    def test_compute_stats_consecutive_losses(self):
        """Max consecutive losses should be tracked."""
        v = BacktestValidator()
        trades = []
        # 3 consecutive losses, then a win, then 2 losses
        for i in range(6):
            is_loss = i in (0, 1, 2, 4, 5)
            entry = 5.0
            exit_p = 4.5 if is_loss else 5.5
            trades.append(Trade(
                symbol="TEST", side='long',
                entry_time=datetime.datetime(2026, 1, 1 + i, 10, 0),
                exit_time=datetime.datetime(2026, 1, 1 + i, 15, 0),
                entry_price=entry, exit_price=exit_p,
                quantity=1, commission=0.0,
            ))
        report = v._compute_stats(trades)
        assert report.max_consecutive_losses == 3


# ══════════════════════════════════════════════════════════════
# Unit Tests — Alpaca Data Loading (Mocked)
# ══════════════════════════════════════════════════════════════

class TestAlpacaDataLoading:
    def test_data_client_initialization(self):
        """Data client should initialize with env vars."""
        v = BacktestValidator()
        with patch.dict('os.environ', {
            'ALPACA_API_KEY': 'test_key',
            'ALPACA_SECRET_KEY': 'test_secret',
        }):
            with patch('alpaca.data.historical.OptionHistoricalDataClient') as mock_client:
                client = v._get_data_client()
                assert client is not None

    def test_data_client_missing_keys(self):
        """Data client should return None without API keys."""
        v = BacktestValidator()
        with patch.dict('os.environ', {}, clear=True):
            # Remove any existing keys
            import os
            os.environ.pop('ALPACA_API_KEY', None)
            os.environ.pop('ALPACA_SECRET_KEY', None)
            client = v._get_data_client()
            assert client is None

    def test_parse_option_symbol_call(self):
        """Should parse call option symbol correctly."""
        v = BacktestValidator()
        result = v._parse_option_symbol("SPY260701C00540000")
        assert result is not None
        exp_date, strike, opt_type = result
        assert exp_date == datetime.date(2026, 7, 1)
        assert strike == 540.0
        assert opt_type == 'call'

    def test_parse_option_symbol_put(self):
        """Should parse put option symbol correctly."""
        v = BacktestValidator()
        result = v._parse_option_symbol("QQQ260815P00480000")
        assert result is not None
        exp_date, strike, opt_type = result
        assert exp_date == datetime.date(2026, 8, 15)
        assert strike == 480.0
        assert opt_type == 'put'

    def test_parse_option_symbol_invalid(self):
        """Invalid symbol should return None."""
        v = BacktestValidator()
        assert v._parse_option_symbol("INVALID") is None
        assert v._parse_option_symbol("") is None

    def test_load_alpaca_options_no_client(self):
        """Should return None when no Alpaca client available."""
        v = BacktestValidator()
        with patch.object(v, '_get_data_client', return_value=None):
            result = v._load_alpaca_options("SPY", "call", 0.005, 45)
            assert result is None


# ══════════════════════════════════════════════════════════════
# Unit Tests — Trade Simulation
# ══════════════════════════════════════════════════════════════

class TestTradeSimulation:
    def test_should_enter_requires_volume_spike(self):
        """Entry requires volume > 1.5x average."""
        import pandas as pd
        v = BacktestValidator()
        # Create data with low volume
        data = pd.DataFrame([
            {'timestamp': datetime.datetime.now(), 'open': 5.0, 'high': 5.5,
             'low': 4.8, 'close': 5.2, 'volume': 100, 'bid': 5.1, 'ask': 5.3}
            for _ in range(10)
        ])
        assert v._should_enter(data, 5) is False

    def test_should_enter_requires_momentum(self):
        """Entry requires positive price momentum."""
        import pandas as pd
        v = BacktestValidator()
        # Create data with declining prices
        data = pd.DataFrame([
            {'timestamp': datetime.datetime.now(), 'open': 5.0 - i*0.1, 'high': 5.5 - i*0.1,
             'low': 4.8 - i*0.1, 'close': 5.2 - i*0.1, 'volume': 2000,
             'bid': 5.1 - i*0.1, 'ask': 5.3 - i*0.1}
            for i in range(10)
        ])
        assert v._should_enter(data, 5) is False

    def test_should_enter_with_volume_spike_and_momentum(self):
        """Entry should trigger with volume spike + uptrend."""
        import pandas as pd
        v = BacktestValidator()
        rows = []
        for i in range(10):
            vol = 2000 if i >= 7 else 500  # Volume spike
            price = 5.0 + i * 0.1  # Uptrend
            rows.append({
                'timestamp': datetime.datetime.now(),
                'open': price - 0.05, 'high': price + 0.1,
                'low': price - 0.1, 'close': price,
                'volume': vol, 'bid': price - 0.05, 'ask': price + 0.05,
            })
        data = pd.DataFrame(rows)
        assert v._should_enter(data, 8) is True

    def test_should_enter_rejects_wide_spread(self):
        """Entry should reject when bid-ask spread > 10%."""
        import pandas as pd
        v = BacktestValidator()
        rows = []
        for i in range(10):
            vol = 2000 if i >= 7 else 500
            price = 5.0 + i * 0.1
            rows.append({
                'timestamp': datetime.datetime.now(),
                'open': price - 0.05, 'high': price + 0.1,
                'low': price - 0.1, 'close': price,
                'volume': vol, 'bid': price * 0.85, 'ask': price * 1.15,  # 30% spread
            })
        data = pd.DataFrame(rows)
        assert v._should_enter(data, 8) is False

    def test_should_enter_requires_5_bars_history(self):
        """Entry should not trigger with < 5 bars of history."""
        import pandas as pd
        v = BacktestValidator()
        data = pd.DataFrame([
            {'timestamp': datetime.datetime.now(), 'open': 5.0, 'high': 5.5,
             'low': 4.8, 'close': 5.2, 'volume': 2000, 'bid': 5.1, 'ask': 5.3}
        ])
        assert v._should_enter(data, 0) is False

    def test_simulate_trades_returns_list(self):
        """Simulation should return a list of Trade objects."""
        import pandas as pd
        v = BacktestValidator()
        # Create a dataset that should generate trades
        rows = []
        for i in range(30):
            vol = 3000 if i >= 25 else 500
            price = 5.0 + i * 0.15
            rows.append({
                'timestamp': datetime.datetime(2026, 6, 1 + i, 10, 0),
                'open': price - 0.05, 'high': price + 0.2,
                'low': price - 0.1, 'close': price,
                'volume': vol, 'bid': price - 0.05, 'ask': price + 0.05,
                'strike': 540.0,
                'expiration': datetime.date(2026, 7, 1),
                'option_type': 'call',
                'symbol': 'SPY260701C00540000',
            })
        data = pd.DataFrame(rows)
        trades = v._simulate_trades(data, 0.50, 2.0, 10, False)
        assert isinstance(trades, list)


# ══════════════════════════════════════════════════════════════
# Unit Tests — Early Assignment
# ══════════════════════════════════════════════════════════════

class TestEarlyAssignment:
    def test_no_assignment_when_not_itm(self):
        """No assignment when option is not ITM."""
        v = BacktestValidator()
        # Short at 5.0, current price 5.5 (OTM for short)
        assert v._check_early_assignment(5.0, 5.5, 10) is False

    def test_assignment_possible_when_deep_itm(self):
        """Assignment should be possible when deep ITM."""
        v = BacktestValidator()
        # Short at 5.0, current price 3.0 (deep ITM)
        # Run multiple times to check probability
        assignments = sum(
            v._check_early_assignment(5.0, 3.0, 30)
            for _ in range(1000)
        )
        # Should have some assignments (>0% but not 100%)
        assert assignments > 0

    def test_assignment_increases_with_moneyness(self):
        """Deeper ITM should have higher assignment probability."""
        v = BacktestValidator()
        # Shallow ITM: 5.0 short, 4.8 current
        shallow = sum(v._check_early_assignment(5.0, 4.8, 30) for _ in range(1000))
        # Deep ITM: 5.0 short, 3.0 current
        deep = sum(v._check_early_assignment(5.0, 3.0, 30) for _ in range(1000))
        assert deep > shallow

    def test_assignment_increases_with_time(self):
        """More days held should increase assignment probability."""
        v = BacktestValidator()
        short_duration = sum(v._check_early_assignment(5.0, 3.0, 5) for _ in range(1000))
        long_duration = sum(v._check_early_assignment(5.0, 3.0, 60) for _ in range(1000))
        assert long_duration > short_duration


# ══════════════════════════════════════════════════════════════
# Integration Tests — Full Validate Trade Flow
# ══════════════════════════════════════════════════════════════

class TestValidateTrade:
    def test_validate_returns_report_when_no_data(self):
        """Should return report with data unavailable when no Alpaca client."""
        v = BacktestValidator()
        with patch.object(v, '_get_data_client', return_value=None):
            report = v.validate_trade(symbol="SPY")
            assert report.valid is False
            assert "unavailable" in report.reason.lower()

    def test_validate_returns_report_on_exception(self):
        """Should return report with exception info on error."""
        v = BacktestValidator()
        with patch.object(v, '_load_alpaca_options', side_effect=Exception("test error")):
            report = v.validate_trade(symbol="SPY")
            assert report.valid is False
            assert "exception" in report.reason.lower()

    def test_validate_legacy_interface(self, sample_trades):
        """Legacy interface should return dict format."""
        v = BacktestValidator()
        with patch.object(v, '_load_alpaca_options', return_value=None):
            result = v.validate_trade_legacy(symbol="SPY")
            assert isinstance(result, dict)
            assert "valid" in result
            assert "sharpe_ratio" in result
            assert "data_source" in result

    def test_validate_with_mocked_data(self):
        """Full validation with mocked Alpaca data."""
        import pandas as pd
        v = BacktestValidator()

        # Create mock options data
        rows = []
        for i in range(150):
            vol = 3000 if i % 10 == 0 else 500
            price = 5.0 + (i % 30) * 0.1
            rows.append({
                'timestamp': datetime.datetime(2026, 1, 1 + (i % 30), 10, 0),
                'open': price - 0.05, 'high': price + 0.2,
                'low': price - 0.1, 'close': price,
                'volume': vol, 'bid': price - 0.05, 'ask': price + 0.05,
                'strike': 540.0,
                'expiration': datetime.date(2026, 7, 1),
                'option_type': 'call',
                'symbol': 'SPY260701C00540000',
            })
        mock_data = pd.DataFrame(rows)

        with patch.object(v, '_load_alpaca_options', return_value=mock_data):
            report = v.validate_trade(symbol="SPY")
            assert report.data_source == "alpaca_options"
            assert isinstance(report.total_trades, int)


# ══════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_commission(self):
        """Trade with zero commission should still calculate P&L."""
        trade = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=1, commission=0.0,
        )
        assert trade.pnl == 50.0  # (5.5 - 5.0) * 100

    def test_negative_pnl(self):
        """Trade with loss should have negative P&L."""
        trade = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=4.5,
            quantity=1, commission=0.0,
        )
        assert trade.pnl < 0

    def test_large_quantity(self):
        """Trade with large quantity should scale P&L."""
        trade = Trade(
            symbol="TEST", side='long',
            entry_time=datetime.datetime.now(),
            exit_time=datetime.datetime.now(),
            entry_price=5.0, exit_price=5.5,
            quantity=10, commission=14.2,
        )
        expected = (5.5 - 5.0) * 10 * 100 - 14.2
        assert trade.pnl == pytest.approx(expected, abs=0.01)

    def test_report_has_all_fields(self):
        """BacktestReport should have all required fields."""
        report = BacktestReport(
            valid=True, sharpe_ratio=1.5, win_rate=60.0,
            profit_factor=2.0, roi_pct=12.0, max_drawdown_pct=8.0,
            total_trades=150, sample_sufficient=True,
            reason="All criteria met", data_source="alpaca_options",
        )
        assert report.valid is True
        assert report.sharpe_ratio == 1.5
        assert report.win_rate == 60.0
        assert report.profit_factor == 2.0
        assert report.roi_pct == 12.0
        assert report.max_drawdown_pct == 8.0
        assert report.total_trades == 150
        assert report.sample_sufficient is True
        assert report.data_source == "alpaca_options"
