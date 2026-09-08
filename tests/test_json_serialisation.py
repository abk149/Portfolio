"""Regression tests for JSON-safe API responses.

The screener 500'd with "Out of range float values are not JSON compliant".
The cause was in `_df`:

    clean.where(pd.notnull(clean), None)

You cannot store None in a float64 column — pandas coerces it straight back to
NaN. So object columns were cleaned and FLOAT columns, exactly the ones holding
missing fundamentals like P/E and ROE, were not. Any screener result with a
missing metric reached Starlette's strict encoder as NaN and blew up.

Also covered: numpy scalars. np.float64 subclasses float so it slips through,
but np.int64 and np.bool_ do not, and those are what broke deploy-cash earlier.
"""
import json

import numpy as np
import pandas as pd
import pytest

from src.dashboard.app import _df, _scrub_for_json


def _messy_frame():
    """A screener-shaped frame: float columns carrying missing metrics."""
    return pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        "tech_score": [72.5, 64.0],
        "PE": [np.nan, 21.3],          # missing fundamental
        "ROE": [np.inf, 15.2],
        "DE": [-np.inf, 0.4],
        "n": np.array([1, 2], dtype="int64"),
        "flag": np.array([True, False]),
    })


def _strict_dumps(obj):
    """Exactly what Starlette does — NaN/Infinity are rejected."""
    return json.dumps(obj, allow_nan=False)


def test_the_old_approach_really_did_leave_nan():
    """Guards the diagnosis: `.where(..., None)` cannot clean a float column."""
    df = _messy_frame().replace([np.inf, -np.inf], np.nan)
    old = df.where(pd.notnull(df), None).to_dict("records")
    assert old[0]["PE"] != old[0]["PE"]                 # still NaN
    with pytest.raises(ValueError):
        _strict_dumps(old)


def test_df_produces_none_for_missing_floats():
    rec = _df(_messy_frame())
    assert rec[0]["PE"] is None
    assert rec[0]["ROE"] is None                        # +Inf
    assert rec[0]["DE"] is None                         # -Inf
    assert rec[1]["PE"] == 21.3                         # real values survive


def test_df_output_serialises_strictly():
    _strict_dumps(_df(_messy_frame()))


def test_numpy_scalars_become_python_natives():
    rec = _df(_messy_frame())
    assert type(rec[0]["n"]) is int
    assert type(rec[0]["flag"]) is bool
    assert type(rec[0]["tech_score"]) is float


def test_scrubber_handles_nesting_and_arrays():
    payload = {
        "a": [np.float64("nan"), np.int64(3), np.bool_(True)],
        "b": {"c": np.array([1.0, np.inf])},
        "d": (np.float32(1.5), None),
    }
    out = _scrub_for_json(payload)
    _strict_dumps(out)
    assert out["a"] == [None, 3, True]
    assert out["b"]["c"] == [1.0, None]


def test_empty_frame_is_fine():
    assert _df(pd.DataFrame()) == []


def test_non_frame_input_is_still_scrubbed():
    assert _scrub_for_json({"x": float("inf")}) == {"x": None}
