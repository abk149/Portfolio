"""The last few quarters, newest first, from the exchange's own filings.

An annual report is up to a year stale when published, so the freshest hard
numbers before a trade are the quarterly filings. Two traps are guarded here,
both of which silently return year-old data as "latest":

  * NSE sends dates as "31-Mar-2024"; sorted as strings, December precedes
    March and the newest filing is whatever happens to sort last;
  * NSE marks filings "Consolidated" or "Non-Consolidated", and a substring
    test for "consolidat" matches BOTH — which put two filings per quarter into
    the consolidated series and let growth be computed across mismatched bases.
"""
from datetime import date, timedelta

import pytest

from src.portfolio import quarters as Q


def _filing(end: str, cons: bool, rev: float, pat: float, eps: float):
    return {"to_date": end, "consolidated": cons, "audited": "Un-Audited",
            "xbrl_url": None, "income_inline": rev * Q.CRORE,
            "profit_inline": pat * Q.CRORE, "eps_inline": eps}


@pytest.fixture()
def feed(monkeypatch):
    """Install a fake NSE feed, bypassing the cache."""
    import src.data.cache as cache
    monkeypatch.setattr(cache, "get_or_set",
                        lambda ns, k, ttl_seconds, fn: fn())

    def _install(rows):
        import src.data.nse_scraper as nse
        monkeypatch.setattr(nse, "financial_results", lambda s, p="Quarterly": rows)
    return _install


# Four consolidated quarters + their year-ago comparatives, listed in a
# deliberately jumbled order.
ROWS = [
    _filing("30-Sep-2024", True, 235481, 19323, 24.48),
    _filing("31-Dec-2023", True, 227971, 19641, 12.27),
    _filing("31-Dec-2024", True, 243865, 21930, 13.70),
    _filing("30-Sep-2023", True, 234956, 19878, 25.18),
    _filing("30-Jun-2024", True, 236217, 17445, 22.37),
    _filing("30-Jun-2023", True, 210831, 18258, 23.39),
    _filing("31-Mar-2024", True, 240715, 21243, 28.01),
    _filing("31-Mar-2023", True, 216376, 21327, 28.12),
]


def test_the_newest_quarter_is_found_despite_string_dates(feed):
    feed(ROWS)
    out = Q.quarterly_results("X", n=4)
    assert out["quarters"][0]["end"] == "2024-12-31"
    assert out["quarters"][0]["label"] == "Q3 FY25"


def test_quarters_come_back_newest_first(feed):
    feed(ROWS)
    ends = [q["end"] for q in Q.quarterly_results("X", n=4)["quarters"]]
    assert ends == sorted(ends, reverse=True)


def test_standalone_filings_are_not_treated_as_consolidated(feed):
    """The substring bug: 'Non-Consolidated' contains 'consolidat'."""
    from src.data.nse_scraper import financial_results
    import src.data.nse_scraper as nse

    raw = [{"toDate": "31-Dec-2024", "consolidated": "Non-Consolidated"},
           {"toDate": "31-Dec-2024", "consolidated": "Consolidated"}]
    nse._get = lambda path, want_json=True, retries=1: raw
    got = financial_results("X", "Quarterly")
    assert [g["consolidated"] for g in got] == [False, True]


def test_only_one_filing_per_quarter_survives(feed):
    """With the flag fixed, the consolidated series has no duplicate periods."""
    mixed = ROWS + [_filing("31-Dec-2024", False, 128260, 8721, 6.44)]
    feed(mixed)
    ends = [q["end"] for q in Q.quarterly_results("X", n=4)["quarters"]]
    assert len(ends) == len(set(ends))


def test_year_on_year_uses_the_same_quarter_not_four_rows_back(feed):
    feed(ROWS)
    q3 = Q.quarterly_results("X", n=4)["quarters"][0]
    assert q3["yoy_label"] == "Q3 FY24"
    assert q3["revenue_yoy_pct"] == pytest.approx(6.97, abs=0.05)
    assert q3["pat_yoy_pct"] == pytest.approx(11.65, abs=0.05)


def test_margins_are_computed_per_quarter(feed):
    feed(ROWS)
    q = Q.quarterly_results("X", n=4)["quarters"][0]
    assert q["net_margin_pct"] == pytest.approx(21930 / 243865 * 100, abs=0.02)


def test_a_stale_feed_is_called_out(feed):
    """Presenting a year-old filing as "the latest quarter" defeats the point
    of reading quarterly numbers at all."""
    feed(ROWS)
    out = Q.quarterly_results("X", n=4)
    assert out["stale"] is True
    assert "months ago" in out["freshness"]


def test_a_current_feed_is_not_called_stale(feed):
    recent = date.today() - timedelta(days=40)
    feed([_filing(recent.strftime("%d-%b-%Y"), True, 1000, 100, 5.0),
          _filing((recent - timedelta(days=91)).strftime("%d-%b-%Y"), True, 900, 80, 4.0)])
    out = Q.quarterly_results("X", n=2)
    assert out["stale"] is False


def test_margin_pressure_is_distinguished_from_growth(feed):
    """Profit lagging sales is the read that matters, and needs several
    quarters to see — it is why this exists rather than one headline number."""
    rows = []
    for i, (rev, pat) in enumerate([(1300, 90), (1200, 88), (1100, 86), (1000, 84)]):
        end = date(2024, 12, 31) - timedelta(days=91 * i)
        rows.append(_filing(end.strftime("%d-%b-%Y"), True, rev, pat, 1.0))
        yago = end - timedelta(days=365)
        rows.append(_filing(yago.strftime("%d-%b-%Y"), True, rev * 0.75, pat * 0.98, 1.0))
    feed(rows)
    t = Q.quarterly_results("X", n=4)["trend"]
    assert t["direction"] == "deteriorating"
    assert "margins under pressure" in t["reading"]


def test_no_filings_is_reported_not_crashed(feed):
    feed([])
    assert "error" in Q.quarterly_results("X", n=4)


def test_documents_prefer_latest_results_over_annual_reports():
    from src.tools.document_fetcher import _rank, _year_hint
    order = sorted(["Annual Report 2026", "Q3 FY25 Financial Results",
                    "Quarterly Results Jan 2025", "Media Release 2026"],
                   key=lambda t: (_rank(t), _year_hint(t)), reverse=True)
    assert order[0].lower().startswith("quarterly")
    assert order.index("Annual Report 2026") > 1
