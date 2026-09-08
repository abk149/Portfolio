"""Cross-verification: keep only social claims that real news confirms.

Reddit is anonymous, unverifiable and often promotional. Feeding it straight
into an LLM that then recommends trades is how a pump-and-dump post becomes an
investment thesis. So nothing from social reaches a decision unless an
independent, named news source is saying substantially the same thing.

**How the matching avoids fooling itself.** Naive keyword overlap would
"corroborate" almost any post, because words like *nifty*, *market* and *stock*
appear in nearly every headline. So the matcher weights terms by how RARE they
are in the current news pool (inverse document frequency): a shared mention of
"nifty" is worth almost nothing, a shared mention of "vodafone" or "glenmark" is
worth a lot. A post is only accepted when the overlap is driven by at least one
genuinely distinctive term — an entity, not a filler word.

Regulator and exchange feeds (SEBI, RBI, PIB, the exchanges) carry extra weight,
because those are primary sources rather than reporting.

The result reports what was rejected as well as what passed. A silent drop looks
identical to "Reddit had nothing to say", and those mean very different things.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from src.utils.logger import get_logger

log = get_logger("tools.corroborate")

# Ordinary English filler plus market vocabulary that is generic in THIS domain
# — "stock" carries no evidential weight in a feed of stock-market headlines.
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "were", "are", "but", "not", "you", "your", "its", "it's", "they", "them",
    "what", "when", "why", "how", "who", "will", "would", "could", "should",
    "can", "may", "about", "after", "before", "into", "over", "under", "than",
    "then", "now", "today", "week", "month", "year", "day", "new", "more",
    "most", "some", "any", "all", "one", "two", "get", "got", "make", "made",
    "just", "like", "much", "many", "very", "here", "there", "out", "off",
    "per", "via", "amid", "says", "said", "see", "seen", "buy", "sell", "hold",
    "stock", "stocks", "share", "shares", "market", "markets", "trading",
    "trade", "investor", "investors", "investment", "price", "prices", "index",
    "nifty", "sensex", "bse", "nse", "india", "indian", "rupee", "crore",
    "lakh", "profit", "loss", "gain", "gains", "high", "low", "up", "down",
    "today's", "anyone", "please", "help", "thoughts", "opinion", "discussion",
    "portfolio", "money", "cash", "fund", "funds", "return", "returns",
}

# Web/markup vocabulary. These survive naive tag-stripping and are rare enough
# in a news pool to score highly, so they must be excluded explicitly.
_STOP |= {
    "href", "https", "http", "www", "com", "org", "net", "html", "amp", "nbsp",
    "span", "div", "img", "src", "alt", "rel", "nofollow", "target", "blank",
    "submitted", "comments", "comment", "permalink", "reddit", "subreddit",
    "utm", "source", "medium", "campaign", "click", "read", "full", "story",
    "article", "link", "links", "photo", "image", "video", "watch", "live",
    "update", "updates", "latest", "news", "report", "reports",
}

_WORD = re.compile(r"[a-z][a-z0-9&']{2,}")
_URL = re.compile(r"https?://\S+|www\.\S+")
# Capitalised tokens in the ORIGINAL casing — a cheap proper-noun detector.
# Sentence-initial words slip in, but they are filtered by the stoplist and the
# phrase requirement, so the net effect is a solid "do these two texts name the
# same thing?" test.
_CAP = re.compile(r"\b([A-Z][A-Za-z0-9&'\-]{2,})\b")


def _entities(text: str) -> set[str]:
    """Proper-noun-ish tokens: companies, people, places, tickers."""
    return {w.lower().strip("-'")
            for w in _CAP.findall(_URL.sub(" ", text or ""))
            if w.lower() not in _STOP}

# Tuning. Deliberately strict — a false corroboration is far more costly than
# a missed one, because it launders an anonymous claim into an apparent fact.
#
# Rarity alone is NOT enough, and assuming it was let a pump post through in
# testing: "ZYQWX will 10x, this is going to fly" matched a headline about
# "FLY-HI MARITIME" on the single word "fly", which is rare in a small news
# pool but carries no evidential weight whatsoever. The fix is to demand
# PHRASE-level agreement (a shared bigram), or failing that several distinctive
# terms at once — a coincidence of one word can no longer verify anything.
# Thresholds must be SCALE-FREE. Raw IDF is not: the same match scores ~4.5
# against a 190-article pool and ~0.5 against a 15-article one, so absolute
# cutoffs tuned on the big pool silently rejected everything from the small
# per-stock bundle that web_search passes in. Both the score and the rarity
# test are therefore normalised by pool size.
MIN_SCORE = 1.0           # on the normalised (0-1 per term) scale
MAX_DF_FRACTION = 0.15    # a term in <=15% of the pool counts as distinctive
WINDOW_HOURS = 96         # news must be near the post in time
MAX_EVIDENCE = 3          # corroborating articles kept per post

# A shared PHRASE is mandatory. A "several distinctive words in common"
# fallback was tried and, on live data, admitted only false positives: a post
# about an ETF's premium was "confirmed" by an unrelated RBI gold-bond notice
# on scattered word overlap. Two documents that genuinely describe the same
# event share a two-word phrase; two that merely share vocabulary do not.


def _clean_text(text: str) -> str:
    return _URL.sub(" ", (text or "").lower())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(_clean_text(text)) if w not in _STOP}


def _bigrams(text: str) -> set[str]:
    """Consecutive significant word pairs — the phrase-level evidence.

    Two documents sharing "reliance jio" or "repo rate" are plausibly about the
    same thing. Two sharing only "fly" are not.
    """
    words = [w for w in _WORD.findall(_clean_text(text)) if w not in _STOP]
    return {f"{a} {b}" for a, b in zip(words, words[1:])}


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _idf(news_tokens: list[set[str]]) -> tuple[dict[str, float], Counter, int]:
    """Normalised inverse document frequency over the news pool.

    A term in nearly every headline scores ~0; a term in one headline scores
    near 1. Dividing by log(n+1) keeps the scale comparable whether the pool is
    15 articles or 500 — which matters, because both sizes occur here.
    """
    n = max(len(news_tokens), 1)
    df: Counter = Counter()
    for toks in news_tokens:
        df.update(toks)
    denom = math.log(n + 1) or 1.0
    idf = {t: (math.log((n + 1) / (c + 1)) + 0.1) / denom for t, c in df.items()}
    return idf, df, n


def corroborate(
    posts: Iterable[dict],
    news: list[dict],
    min_score: float = MIN_SCORE,
    window_hours: int = WINDOW_HOURS,
) -> dict:
    """Split social posts into corroborated and rejected.

    `posts` need `title` (and ideally `snippet`, `created_utc`).
    `news`  need `title` (and ideally `source`, `url`, `published_dt`, `weight`).
    """
    # The same story is often cross-posted to several subs; dedupe on the
    # normalised title so it isn't verified (and shown to the model) twice.
    seen_posts: set[str] = set()
    deduped: list[dict] = []
    for p in (posts or []):
        key = re.sub(r"[^a-z0-9]+", "", (p.get("title") or "").lower())[:80]
        if not key or key in seen_posts:
            continue
        seen_posts.add(key)
        deduped.append(p)
    posts = deduped
    news = list(news or [])

    if not posts:
        return {"verified": [], "rejected": [],
                "counts": {"in": 0, "verified": 0, "rejected": 0},
                "news_pool": len(news),
                "note": "No social posts to check."}

    if not news:
        # No evidence pool means nothing CAN be corroborated. Rejecting
        # everything is the correct, safe answer — not a reason to let it all
        # through.
        return {
            "verified": [],
            "rejected": [{"title": p.get("title"), "subreddit": p.get("subreddit"),
                          "reason": "no news pool available to verify against"}
                         for p in posts],
            "counts": {"in": len(posts), "verified": 0, "rejected": len(posts)},
            "news_pool": 0,
            "note": "No news could be fetched, so nothing from social could be "
                    "verified. All social input was discarded rather than used "
                    "unchecked.",
        }

    news_tokens = [_tokens(f"{n.get('title','')} {n.get('snippet','')}") for n in news]
    news_bigrams = [_bigrams(f"{n.get('title','')} {n.get('snippet','')}") for n in news]
    news_ents = [_entities(f"{n.get('title','')} {n.get('snippet','')}") for n in news]
    idf, df, n_docs = _idf(news_tokens)
    # Scale-free rarity: "in at most 15% of the pool", floored at 1 document so
    # a tiny pool still has a meaningful notion of distinctive.
    max_df = max(1, int(MAX_DF_FRACTION * n_docs))
    news_dts = [_parse_dt(n.get("published_dt") or n.get("published")) for n in news]

    verified: list[dict] = []
    rejected: list[dict] = []

    for p in posts:
        blob = f"{p.get('title','')} {p.get('snippet','')}"
        p_tokens = _tokens(blob)
        p_bigrams = _bigrams(blob)
        p_ents = _entities(blob)
        if len(p_tokens) < 2:
            rejected.append({"title": p.get("title"), "subreddit": p.get("subreddit"),
                             "reason": "post has no substantive content to verify"})
            continue
        p_dt = _parse_dt(p.get("created_utc") or p.get("published"))

        scored: list[tuple[float, int, list[str], list[str], list[str]]] = []
        for i, n_toks in enumerate(news_tokens):
            shared = p_tokens & n_toks
            if not shared:
                continue
            if p_dt and news_dts[i]:
                if abs((p_dt - news_dts[i]).total_seconds()) > window_hours * 3600:
                    continue
            rare = [t for t in shared if df.get(t, 0) <= max_df]
            if not rare:
                continue
            weight = float(news[i].get("weight") or 1.0)
            score = sum(idf.get(t, 0.0) for t in shared) * weight
            phrases = sorted(p_bigrams & news_bigrams[i])

            # Mandatory, all three:
            #   a shared phrase (same wording, not just shared vocabulary),
            #   a shared named entity (the two texts are about the same thing),
            #   enough weighted overlap.
            # The entity test is what rejects matches on generic phrases like
            # "gap between", which otherwise linked an ETF-premium question to
            # an unrelated corporate-confidence survey.
            shared_ents = p_ents & news_ents[i]
            if not phrases or not shared_ents or score < min_score:
                continue

            scored.append((score, i,
                           sorted(shared, key=lambda t: -idf.get(t, 0.0))[:6],
                           phrases[:3], sorted(shared_ents)[:4]))

        scored.sort(reverse=True, key=lambda x: x[0])
        if scored:
            evidence = []
            for score, i, terms, phrases, ents in scored[:MAX_EVIDENCE]:
                evidence.append({
                    "source": news[i].get("source", "?"),
                    "title": news[i].get("title", ""),
                    "url": news[i].get("url", ""),
                    "published": news[i].get("published_dt") or news[i].get("published"),
                    "matched_terms": terms,
                    "matched_phrases": phrases,
                    "matched_entities": ents,
                    "score": round(score, 2),
                })
            item = dict(p)
            item["corroboration"] = {
                "score": round(scored[0][0], 2),
                "sources": evidence,
                "source_count": len({e["source"] for e in evidence}),
            }
            verified.append(item)
        else:
            rejected.append({
                "title": p.get("title"), "subreddit": p.get("subreddit"),
                "reason": "no independent news confirms this — no article "
                          "shares both a distinctive phrase and a named entity "
                          "with it",
            })

    verified.sort(key=lambda x: -x["corroboration"]["score"])
    return {
        "verified": verified,
        "rejected": rejected,
        "counts": {"in": len(posts), "verified": len(verified),
                   "rejected": len(rejected)},
        "news_pool": len(news),
        "note": (f"{len(verified)} of {len(posts)} social posts were independently "
                 f"confirmed by the news pool ({len(news)} articles) and kept; "
                 f"the rest were discarded, not shown to the model."),
    }
