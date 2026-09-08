"""Every endpoint, exercised against awkward-but-realistic data.

This exists because the same class of bug kept reaching the device: a response
that was fine on a clean day and 500'd the moment a value was NaN, ±Inf, a numpy
scalar, or a price frame arrived with a different timezone from its neighbour.
Each was found by hand, one device round-trip at a time.

So: hit every route, and assert two things that no endpoint may ever violate —
it must not raise, and its response must survive the STRICT JSON encoder that
Starlette actually uses. `_scrub_for_json` is not consulted; the wire format is.
"""
import json

import pytest

# Routes that would do something irreversible, cost money, or need a real
# browser round-trip. Excluded deliberately, and listed so the exclusion is
# visible rather than implied by silence.
SKIP = {
    "/api/quant/run",              # spawns a subprocess
    "/api/universe-map/build",     # long crawl
    "/api/universe-map/reset",     # destructive
    "/api/kb/upload",              # multipart
    "/api/portfolio/upload_trades",# multipart
    "/api/telegram/send-report",   # sends a real message
    "/api/telegram/test",          # sends a real message
    "/api/telegram/bot/start",     # spawns a bot
    "/api/scheduler/start",        # background scheduler
    "/api/groww/login",            # live auth
    "/api/upstox/exchange-code",   # live auth
    "/api/ghost/reset",            # destructive
    "/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json", "/",
}

# Minimal valid bodies for routes that need one.
BODIES = {
    "/api/screener/scan": {"universe": "nifty50", "tech_min": 0, "fund_min": 0},
    "/api/intraday/scan": {"universe": "nifty50", "min_score": 0},
    "/api/intraday/analyze": {"days": 30},
    "/api/portfolio/optimize": {"mode": "max_sharpe", "max_weight": 0.4},
    "/api/portfolio/deploy-cash": {"cash": 25000, "include_universe": False},
    "/api/portfolio/benchmark": {"index": "^NSEI", "window_days": 365},
    "/api/themes": {"days": 7},
    "/api/deep-dive": {"symbol": "AAA"},
    "/api/chat": {"message": "hello"},
    "/api/agent": {"agent": "portfolio", "question": "hi"},
    "/api/kb/search": {"query": "test"},
    "/api/kb/search-decisions": {"query": "test"},
    "/api/kb/ingest-text": {"text": "hello", "title": "t"},
    "/api/kb/export-finetune": {},
    "/api/calendar": {"days_ahead": 7, "days_back": 2},
    "/api/ghost/buy": {"symbol": "AAA", "amount": 5000},
    "/api/ghost/sell": {"id": "nope"},
    "/api/ghost/review": {"with_ai": False},
    "/api/broker": {"broker": "upstox"},
    "/api/llm/config": {"api_key": "", "model": ""},
    "/api/upstox/config": {"api_key": "k", "api_secret": "s",
                           "redirect_uri": "http://127.0.0.1:8000/callback"},
    "/api/groww/save-token": {"token": "t"},
    "/api/ai/event-impact": {"event": {"title": "T", "date": "2026-09-16",
                                       "weekday": "Wed", "region": "US",
                                       "importance": "HIGH", "why": "w",
                                       "certainty": "confirmed", "source": "s"}},
}

PATH_PARAMS = {"job_id": "nosuchjob", "doc_id": "nosuchdoc", "job_name": "nosuchjob"}


def _routes(app):
    out = []
    for r in app.routes:
        if not hasattr(r, "methods"):
            continue
        path = r.path
        for name, val in PATH_PARAMS.items():
            path = path.replace("{" + name + "}", val)
        method = sorted(r.methods - {"HEAD", "OPTIONS"})[0]
        if r.path in SKIP or method == "DELETE":
            continue
        out.append((method, r.path, path))
    return sorted(set(out))


def _strict(obj):
    """Exactly the encoder Starlette uses — NaN/Infinity are rejected."""
    return json.dumps(obj, allow_nan=False)


def test_every_route_answers_json(api):
    client, A = api
    failures = []
    for method, template, path in _routes(A.app):
        body = BODIES.get(template)
        try:
            resp = (client.get(path) if method == "GET"
                    else client.post(path, json=body if body is not None else {}))
        except Exception as e:                       # noqa: BLE001
            failures.append(f"{method} {template}: RAISED {type(e).__name__}: {e}")
            continue

        if resp.status_code >= 500:
            failures.append(f"{method} {template}: HTTP {resp.status_code} "
                            f"{resp.text[:160]}")
            continue
        # A 4xx is a legitimate answer (bad input, missing resource). A JSON
        # body that cannot be encoded is never legitimate. Endpoints that serve
        # a file or a page are checked for status only.
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            continue
        try:
            _strict(resp.json())
        except ValueError as e:
            failures.append(f"{method} {template}: body not JSON-safe: {e}")

    assert not failures, "Endpoints returned unusable responses:\n  " + \
                         "\n  ".join(failures)


def test_job_results_are_json_safe(api):
    """Background jobs serialise separately from the submit call.

    The screener crash lived here: submitting returned a clean {"job_id": ...}
    and the NaN only surfaced later, when the RESULT was fetched.
    """
    import time
    client, A = api
    job_routes = {
        "/api/screener/scan": BODIES["/api/screener/scan"],
        "/api/portfolio/optimize": BODIES["/api/portfolio/optimize"],
        "/api/portfolio/performance": {},
        "/api/ghost/curve": {},
    }
    failures = []
    for path, body in job_routes.items():
        r = client.post(path, json=body).json()
        jid = r.get("job_id")
        if not jid:
            continue                                  # answered inline
        for _ in range(100):
            o = client.get(f"/api/jobs/{jid}")
            if o.status_code >= 500:
                failures.append(f"{path}: job fetch HTTP {o.status_code} "
                                f"{o.text[:200]}")
                break
            data = o.json()
            if data.get("status") != "running":
                try:
                    _strict(data)
                except ValueError as e:
                    failures.append(f"{path}: job result not JSON-safe: {e}")
                if data.get("status") == "error":
                    failures.append(f"{path}: job errored: {data.get('error')}")
                break
            time.sleep(0.1)
    assert not failures, "Job results unusable:\n  " + "\n  ".join(failures)
