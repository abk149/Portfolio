"""Regression tests for intraday trade analysis.

`df.get("trade_date") or df.get("order_timestamp")` calls bool() on a Series
and raised "The truth value of a Series is ambiguous" for every user who had
trades — column selection must be by name, never by truthiness. The analyzer
also has to cope with brokers that name their fields differently.
"""
import pandas as pd
import pytest

from src.intraday.analyzer import IntradayAnalyzer


def _analyze(rows):
    an = IntradayAnalyzer.__new__(IntradayAnalyzer)      # skip broker init
    an._fetch = lambda days: IntradayAnalyzer._normalise(pd.DataFrame(rows))
    return an.analyze(90)


UPSTOX_ROWS = [
    {"trade_date": "2026-09-01", "order_timestamp": "2026-09-01T10:00",
     "quantity": 10, "price": 100.0, "transaction_type": "BUY", "tradingsymbol": "ALPHA"},
    {"trade_date": "2026-09-01", "order_timestamp": "2026-09-01T14:00",
     "quantity": 10, "price": 106.0, "transaction_type": "SELL", "tradingsymbol": "ALPHA"},
    {"trade_date": "2026-09-02", "order_timestamp": "2026-09-02T10:00",
     "quantity": 5, "price": 200.0, "transaction_type": "BUY", "tradingsymbol": "BETA"},
    {"trade_date": "2026-09-02", "order_timestamp": "2026-09-02T15:00",
     "quantity": 5, "price": 190.0, "transaction_type": "SELL", "tradingsymbol": "BETA"},
]

# Same trades, a different broker's field names.
GROWW_ROWS = [
    {"trade_time": "2026-09-03 09:30:00", "trade_qty": 8, "trade_price": 50.0,
     "trade_type": "B", "trading_symbol": "GAMMA"},
    {"trade_time": "2026-09-03 15:10:00", "trade_qty": 8, "trade_price": 55.0,
     "trade_type": "S", "trading_symbol": "GAMMA"},
]


def test_both_date_columns_present_does_not_raise():
    """The exact shape that triggered the ambiguous-Series error."""
    stats = _analyze(UPSTOX_ROWS)
    assert stats["trades"] == 2


def test_pnl_and_profit_factor_are_correct():
    stats = _analyze(UPSTOX_ROWS)
    assert stats["total_pnl"] == 10.0          # +60 on ALPHA, -50 on BETA
    assert stats["win_rate_pct"] == 50.0
    assert stats["profit_factor"] == 1.2       # 60 / 50


def test_alternate_broker_field_names():
    stats = _analyze(GROWW_ROWS)
    assert stats["trades"] == 1
    assert stats["total_pnl"] == 40.0          # 8 x 5


def test_unusable_rows_are_dropped_not_fatal():
    rows = GROWW_ROWS + [{"trade_time": None, "trade_qty": 1, "trade_price": None,
                          "trade_type": "B", "trading_symbol": "JUNK"}]
    assert _analyze(rows)["trades"] == 1


def test_missing_fields_give_a_clear_error():
    with pytest.raises(ValueError, match="missing required field"):
        _analyze([{"foo": 1, "bar": 2}])


def test_no_losses_gives_no_profit_factor_not_a_divide_by_zero():
    stats = _analyze(GROWW_ROWS)
    assert stats["profit_factor"] is None
