"""Regression tests for social-claim corroboration.

These encode failures found on live data, each of which let an anonymous claim
through as if it were a fact:

  * a pump post ("ZYQWX will 10x ... going to fly") was "confirmed" by a
    headline about FLY-HI MARITIME, on the single shared word "fly";
  * a post was confirmed on the shared phrase "href https" — HTML markup
    leaking out of Reddit's Atom <content> and into the matcher;
  * an ETF-premium question was confirmed by an unrelated corporate-confidence
    survey on the generic phrase "gap between";
  * and, after tightening, a GENUINE match was rejected because the thresholds
    were tuned against a 190-article pool and web_search passes ~15 items.

Run: python -m pytest tests/ -q      (no network required)
"""
from src.tools.corroborate import corroborate

NAMED_SOURCES = [
    {"title": "Glenmark Pharma gets US FDA approval for generic drug",
     "source": "Moneycontrol",
     "snippet": "Glenmark Pharmaceuticals received final approval"},
    {"title": "Nifty ends higher led by pharma stocks",
     "source": "Economic Times", "snippet": "pharma gained"},
    {"title": "Tata Motors launches tender offer for Iveco Group",
     "source": "Economic Times", "snippet": "deal values the company"},
]


def _verified_titles(posts, news):
    return {v["title"] for v in corroborate(posts, news)["verified"]}


def test_post_matching_a_named_source_is_kept():
    post = {"title": "Glenmark Pharma gets US FDA approval for generic drug",
            "snippet": "confirmed", "subreddit": "x"}
    assert post["title"] in _verified_titles([post], NAMED_SOURCES)


def test_pump_post_on_a_real_ticker_is_rejected():
    post = {"title": "GLENMARK will 5x, insider tip, load up now",
            "snippet": "trust me bro", "subreddit": "x"}
    assert not _verified_titles([post], NAMED_SOURCES)


def test_single_coincidental_word_does_not_corroborate():
    """The FLY-HI failure: one shared rare word is a coincidence, not evidence."""
    news = [{"title": "Change in group of equity shares of FLY-HI Maritime Travels",
             "source": "BSE", "snippet": ""}]
    post = {"title": "ZYQWX Industries will 10x next week",
            "snippet": "insider info, this is going to fly", "subreddit": "x"}
    assert not _verified_titles([post], news)


def test_html_markup_cannot_corroborate():
    """The 'href https' failure: markup must never count as shared content."""
    news = [{"title": "Some market story", "source": "ET",
             "snippet": 'read more <a href="https://www.example.com">here</a>'}]
    post = {"title": "PC Jewellers up 43% in a month",
            "snippet": "href https www example com submitted by", "subreddit": "x"}
    assert not _verified_titles([post], news)


def test_generic_phrase_without_shared_entity_is_rejected():
    """The 'gap between' failure: same words, different subject."""
    news = [{"title": "Why India Inc is more confident about growth in Q2",
             "source": "CNBC-TV18",
             "snippet": "the gap between expectation and delivery narrowed"}]
    post = {"title": "Why is MON100 trading at such a huge premium to its iNAV?",
            "snippet": "big gap between price and value", "subreddit": "x"}
    assert not _verified_titles([post], news)


def test_generic_market_chatter_is_rejected():
    post = {"title": "Nifty looking weak today, thoughts on the market?",
            "snippet": "any views on stocks", "subreddit": "x"}
    assert not _verified_titles([post], NAMED_SOURCES)


def test_empty_news_pool_rejects_everything():
    """No evidence means nothing is verified — never 'let it through'."""
    post = {"title": "Glenmark Pharma gets US FDA approval", "snippet": "", "subreddit": "x"}
    out = corroborate([post], [])
    assert out["counts"]["verified"] == 0
    assert out["counts"]["rejected"] == 1


def test_thresholds_are_scale_free():
    """The same true match must pass against a 3-item and a 300-item pool.

    Raw IDF is pool-size dependent; cutoffs tuned on a big pool silently
    rejected everything from the small per-stock bundle.
    """
    post = {"title": "Glenmark Pharma gets US FDA approval for generic drug",
            "snippet": "confirmed", "subreddit": "x"}
    padding = [{"title": f"Unrelated market story number {i} about various sectors",
                "source": "Filler", "snippet": "generic market commentary"}
               for i in range(300)]
    assert post["title"] in _verified_titles([post], NAMED_SOURCES)
    assert post["title"] in _verified_titles([post], NAMED_SOURCES + padding)


def test_cross_posted_duplicates_are_collapsed():
    post = {"title": "Glenmark Pharma gets US FDA approval for generic drug",
            "snippet": "confirmed", "subreddit": "a"}
    dupe = dict(post, subreddit="b")
    out = corroborate([post, dupe], NAMED_SOURCES)
    assert out["counts"]["in"] == 1
    assert len(out["verified"]) == 1
