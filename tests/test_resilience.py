"""Regression tests for the failures that made fixed bugs look like they returned.

Every case here was found by running the API against the phone's exact library
stack (pandas 2.1.3, fastapi 0.99.1, no scipy, no pyarrow) rather than the dev
machine's. None of them reproduce on the dev stack, which is precisely why they
kept reaching the device.
"""
import pickle
import sys

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# The poison-pill cache
# ---------------------------------------------------------------------------
def test_unreadable_cache_entry_is_a_miss_not_an_error(tmp_path, monkeypatch):
    """A cache file that cannot be unpickled must not break the caller.

    This is the mechanism that made fixed bugs "come back": get_or_set called
    pickle.loads bare, so one bad file re-raised the same exception on every
    single run until somebody deleted it by hand. A DataFrame pickled by
    pandas 3.x references pyarrow; loading it on the phone (pandas 2.1.3, no
    pyarrow) raises ModuleNotFoundError, and the Performance tab failed every
    time until the cache was cleared.
    """
    import src.data.cache as cache
    monkeypatch.setattr(cache, "_path", lambda ns, key: tmp_path / f"{ns}_{key}.pkl")

    bad = tmp_path / "ns_key.pkl"
    bad.write_bytes(b"this is not a pickle at all")

    calls = []
    out = cache.get_or_set("ns", "key", 3600, lambda: (calls.append(1), "fresh")[1])
    assert out == "fresh", "should have refetched"
    assert calls == [1]
    # The poison is gone: the file now holds the fresh value, so the NEXT call
    # is a clean hit rather than the same exception again.
    assert pickle.loads(bad.read_bytes()) == "fresh"
    assert cache.get_or_set("ns", "key", 3600, lambda: pytest.fail("should hit cache")) \
        == "fresh"


def test_cache_entry_referencing_a_missing_module_is_discarded(tmp_path, monkeypatch):
    """The exact pyarrow shape, simulated without needing pandas 3."""
    import src.data.cache as cache
    monkeypatch.setattr(cache, "_path", lambda ns, key: tmp_path / f"{ns}_{key}.pkl")
    p = tmp_path / "ns_key.pkl"
    # A pickle that imports a module which does not exist.
    p.write_bytes(b"\x80\x04\x95\x1f\x00\x00\x00\x00\x00\x00\x00"
                  b"\x8c\x0enot_a_real_mod\x94\x8c\x03Foo\x94\x93\x94.")
    assert cache.get_or_set("ns", "key", 3600, lambda: "fresh") == "fresh"


def test_cache_write_failure_does_not_break_the_caller(tmp_path, monkeypatch):
    import src.data.cache as cache
    monkeypatch.setattr(cache, "_path", lambda ns, key: tmp_path / "nope" / "x.pkl")
    assert cache.get_or_set("ns", "key", 3600, lambda: "value") == "value"


# ---------------------------------------------------------------------------
# One missing quote must not NaN the whole book
# ---------------------------------------------------------------------------
def test_one_unpriced_holding_does_not_nan_the_whole_report():
    """A single holding with no live price used to make total value, P&L and
    percentage all NaN — the entire Performance tab read blank."""
    from src.portfolio.analytics import PerformanceAnalyzer

    class Broker:
        def holdings(self):
            return [
                {"tradingsymbol": "AAA", "quantity": 10, "average_price": 100.0,
                 "last_price": 110.0},
                {"tradingsymbol": "BBB", "quantity": 5, "average_price": 200.0,
                 "last_price": float("nan")},          # no quote
            ]
        def _get(self, *a, **k): return []
        def candles(self, *a, **k): return pd.DataFrame()

    pa = PerformanceAnalyzer(upstox=Broker())
    r = pa.full_report()
    s = r["summary"]
    assert not np.isnan(s["current_value"])
    assert s["current_value"] == 1100.0 + 1000.0      # BBB held at cost
    assert s["unpriced_holdings"] == ["BBB"]          # and the gap is reported


# ---------------------------------------------------------------------------
# Optional desktop-only dependencies
# ---------------------------------------------------------------------------
def test_scheduler_jobs_import_without_apscheduler():
    """The job functions are plain callables; importing them must not require
    the scheduler library, which the APK does not ship."""
    import src.scheduler.jobs as jobs
    assert callable(jobs.job_macro_check)


def test_build_stamp_is_exposed():
    """So 'is the fix actually on the device?' is a glance, not a rebuild."""
    import importlib
    A = importlib.import_module("src.dashboard.app")
    A = sys.modules["src.dashboard.app"]
    b = A._build_info()
    assert set(b) == {"id", "built_at"} and b["id"]
