# Portfolio Quant — Indian‑Equities Intelligence Platform

An end‑to‑end quant & portfolio‑intelligence platform for Indian markets — a
modular Python engine, a FastAPI web dashboard, **and a native Android app that
runs the *entire* Python backend on‑device** (via Chaquopy). One codebase powers
the desktop and the phone.

> Personal research project. **Not investment advice.** No credentials, keys, or
> personal data are contained in this repository.

---

## What it does

- **Portfolio analytics** — holdings/positions, live P&L, allocation, day‑change,
  concentration risk, and underperformer detection.
- **MPT optimization** — max‑Sharpe / min‑variance / target‑return portfolios,
  the **efficient frontier**, and a *deploy‑cash* optimizer that suggests how to
  invest a fresh amount (₹ **and** whole shares) to move you toward the frontier.
- **Performance & attribution** — reconstructs your portfolio value **over time**
  from executed orders, money‑weighted return (**XIRR**), winners/losers, and
  "sold‑too‑early" opportunity misses.
- **Ghost portfolio (paper trading)** — the point is to score the *engines*, not
  your own instincts, so recommendations arrive on their own: every pick from
  Macro Ideas, the DR-Quant funnel and the cash optimiser lands in a queue with
  a **Buy** button and the amount the engine itself proposed. Each position keeps
  its provenance, so a **scorecard by engine** shows which one is actually worth
  listening to. The queue can be **sized against your real holdings** — every
  pending idea goes through the same buy-only optimiser, so a Macro Ideas pick
  and an optimiser pick are quoted in one currency (₹, whole shares, and share
  of the book) instead of a flat default beside a real allocation. A name the
  optimiser won't fund alongside what you own says so, which is an answer — P&L, return and hit rate per source, with your own manual picks
  tracked separately so they can't be credited to an engine. Positions book at
  the **live price** (you don't get to pick the fill, or the record proves
  nothing) and are then tracked with the same machinery as the real book. Two charts: your real book, the paper book and the two combined; and the
  ghost book standalone. Closed positions keep their realised P&L, so the track
  record includes what went wrong. A **sell review** runs the same exit
  discipline a real position gets — RSI, moving averages, give-back from the
  high, time held, all computed deterministically — then weighs those against
  the market regime and the events on the calendar.
- **Benchmark vs the index** — **time‑weighted** return (deposits and
  withdrawals are stripped out, so new money is never mistaken for performance)
  against NIFTY 50 / SENSEX / NIFTY BANK / NIFTY 100: rebased growth curve,
  **rolling trailing‑12‑month return**, month‑by‑month bars, and the full risk
  picture — alpha, beta, correlation, tracking error, up/down capture and max
  drawdown on both legs.
- **Market calendar** — the events that move the book, ahead of time: FOMC dates
  scraped from **the Fed's own calendar**, RBI MPC decisions from **RBI press
  releases**, and the recurring macro prints (CPI, payrolls, PCE, IIP, GDP, PMI,
  results season, F&O expiry) generated from each agency's published schedule.
  Every row is labelled **confirmed** (from the issuing body) or **expected**
  (pattern‑derived), so a guess is never dressed up as a fact. Alongside it, a
  market‑filtered news bulletin round‑robins ~20 feeds so no single source
  floods it.
- **Screener** — a two‑stage funnel: technical (RSI/MACD/EMA/ATR/volume) →
  fundamental (P/E, ROE, D/E, growth) scoring with buy/hold/avoid calls.
- **DR‑Quant funnel** — a multi‑stage, LLM‑assisted research pipeline over a
  scored stock universe. Survivors are presented as per‑stock dossier cards
  (health score, thesis, the metrics the model actually returned, key risks),
  not a wide table, alongside a market‑backdrop panel (India VIX, USD/INR,
  Nifty move, PCR) that says *why* a reading is missing rather than showing a
  blank tile.
- **Universe Map** — a crawler that scores the whole universe (technical +
  fundamental) into a persistent knowledge base, visualized as a four‑quadrant
  map of balance‑sheet quality against price action (*Leaders · Momentum only ·
  Out of favour · Weak on both*), with a live tap‑to‑filter table. The crawl is
  **resumable**: it checkpoints to disk as it goes and reuses anything still
  fresh, so a first full‑NSE build can be interrupted and picked up later
  instead of starting over.
- **Macro Ideas** — ingests **recent, date‑filtered** signals from **40+ live
  sources** — business dailies, wires, and primary feeds from SEBI, RBI, PIB,
  NSE and BSE — fetched in parallel with **per‑source health tracking**, plus a
  live macro snapshot (VIX / PCR / USD‑INR). An LLM then weighs **all** factors
  into one *holistic* view and returns 3–7 conviction‑ranked picks, each with
  sector, a **quant entry price**, and a multi‑factor thesis.
- **Social claims are cross‑verified, never trusted** — Reddit is included only
  where a post is independently corroborated by a named news source. Matching
  requires a shared *phrase* **and** a shared *named entity*, so a coincidental
  word can't launder an anonymous pump post into an apparent fact. Whatever
  fails the check is discarded before the model sees it, and the drop is
  reported rather than silent. Covered by regression tests built from real
  false positives.
- **Deep dive** — for any stock: pulls the last two quarters' results /
  earnings‑call PDFs, extracts the text, and produces a skeptical equity‑research
  read (financial‑health issues, valuation, red flags) plus a technical entry
  zone (DMAs / support / RSI / ATR).
- **Knowledge base** — SQLite + FTS5 full‑text search (zero native deps),
  optional embeddings.
- **AI assistant** — a chat grounded in *your* loaded data (portfolio, latest
  DR‑Quant run, Universe Map, benchmark stats and upcoming events).
- **AI applications over your own numbers** — four grounded LLM features that
  turn computed data into decisions, each told to use only what it is given and
  to say when something is missing:
  - **Morning brief** — the calendar and the news read *through your holdings*:
    which events touch which of your names, and what to watch this week.
  - **Performance review** — why you are beating or trailing the index, whether
    the extra return justified the extra risk, and which habits (sell discipline,
    concentration) are costing you.
  - **Risk pre‑mortem** — largest concentration, which holdings would fall
    together on a shared driver, and which events would hit several at once.
  - **Event impact** — tap any calendar row: what it is, your exposure, and the
    transmission both ways if it surprises.

## Highlights (engineering)

- **One backend, two surfaces.** The same FastAPI + Python engine serves the
  desktop web dashboard and runs **inside the Android app** via Chaquopy —
  Gradle syncs `src/` + `config/` into the APK at build time.
- **Nothing blocks.** The app has no modal dialogs: every long analysis is
  submitted to a process-wide job registry and tracked in an embedded activity
  bar, so you can start a deep dive, navigate elsewhere, and come back to it.
  Work continues while the app is minimised — it runs in a foreground service
  whose notification reports what is in flight.
- **Native Android UI** in Jetpack Compose (Material 3): portfolio, ideas,
  calendar, DR‑Quant, universe map, analysis, settings, an in‑app system
  terminal, and native charts drawn on Canvas (donut / multi‑series line with a
  real ₹ axis / paired bars / efficient‑frontier / scatter).
- **Correct return maths.** Portfolio value is not a return series — it moves
  when you deposit. Everything comparative is chain‑linked **time‑weighted
  return**, with external cash flow removed period by period, which is the only
  basis on which "me vs NIFTY" means anything.
- **Broker‑agnostic** — pluggable brokers (**Upstox** + **Groww**, either/or)
  behind one interface; Upstox OAuth (browser + auto‑capture) and Groww **TOTP**
  daily‑token login that self‑heals across the 6 AM reset.
- **Fail‑proof market data** — a free public price source (Yahoo) transparently
  backs up the broker feed, with a circuit breaker that stops hammering a broker
  that can't serve data mid‑run.
- **Pluggable LLM** — NVIDIA NIM (cloud) as the primary brain with an optional
  local fallback chain; a thin provider abstraction (`complete` / `tool_loop`).

## Architecture

```
┌───────────────────────────┐        ┌──────────────────────────────┐
│  Web dashboard (browser)  │        │   Android app (Jetpack Compose)│
│  static + Chart.js        │        │   native UI + system terminal  │
└─────────────┬─────────────┘        └───────────────┬────────────────┘
              │  HTTP (127.0.0.1:8000)                │ HTTP (on‑device)
              ▼                                       ▼
        ┌───────────────────────────────────────────────────┐
        │              FastAPI backend (src/dashboard)        │
        └───────────────────────────────────────────────────┘
              │            │            │            │
      ┌───────┘      ┌─────┘      ┌─────┘      ┌─────┘
      ▼              ▼            ▼            ▼
  brokers/       portfolio/   screener/    agents/ + tools/ + llm/
  data/          (MPT, perf)  intraday/    kb/  (macro, deep‑dive)
      │                                        │
      ▼                                        ▼
  Upstox / Groww  ◄── Yahoo fallback      NVIDIA NIM (LLM)
```

## Tech stack

Python · FastAPI · pandas / numpy · SQLite + FTS5 · Typer CLI ·
Kotlin · Jetpack Compose (Material 3) · Chaquopy · OkHttp · Gradle ·
NVIDIA NIM (OpenAI‑compatible) · Upstox & Groww trading APIs.

## Module map

| Path | Responsibility |
| ---- | -------------- |
| `src/brokers/` | Broker abstraction + factory (Upstox / Groww), Groww auth (TOTP/checksum) |
| `src/upstox/` | Upstox OAuth, REST client, data models |
| `src/data/` | Market data (broker + Yahoo fallback), circuit breaker, caching |
| `src/portfolio/` | Holdings/positions, P&L, MPT optimizer, performance/XIRR, time‑weighted benchmark |
| `src/screener/` | Technical + fundamental scoring engine |
| `src/intraday/` | Historical trade analyzer + live scanner |
| `src/universe_map/` | Whole‑universe crawler → knowledge base |
| `src/kb/` | SQLite + FTS5 knowledge base, optional embeddings |
| `src/agents/` | LLM‑orchestrated agents (portfolio / screener / intraday / quant) |
| `src/llm/` | Provider abstraction (NVIDIA / fallback chain) + grounded insight applications |
| `src/tools/` | Fundamentals, news/RSS, Reddit, macro snapshot, market calendar, deep‑dive, PDF |
| `src/dashboard/` | FastAPI app + web UI |
| `android/` | Native Jetpack Compose app (runs the Python backend on‑device) |
| `tests/` | Regression tests for the corroboration matcher and trade analysis |
| `main.py` | Typer CLI entrypoint |

Every module is independent — import and use any piece without the CLI or agents.

## Durability

Engine results (Macro Ideas, DR-Quant, cash allocation, performance, calendar)
are written to disk as they're produced and restored on open with their age
shown — these take minutes and cost network and LLM calls, and used to live only
in an in-memory job table, so any restart threw them away. Anything older than
**60 days is deleted rather than shown**: a two-month-old "current" view is
worse than an empty screen, because it looks fresh.

## Testing

```bash
./run_tests.sh          # both library stacks
./verify_apk.sh         # prove the APK contains the code you just wrote
```

The suite runs against **two** stacks: the dev machine's, and the exact one
Chaquopy ships in the APK (pandas 2.1.3, FastAPI 0.99.1, no scipy, no pyarrow).
That is not belt-and-braces — several bugs only ever appeared on the phone's
stack, including a `resample("ME")` alias that arrived in pandas 2.2 and a
cached pickle that needed pyarrow. Testing only the dev stack sent those to the
device to be found by hand.

`tests/test_api_smoke.py` walks **every** route with deliberately awkward data —
NaN, ±Inf, numpy scalars, and price frames whose timezones disagree — and
asserts two things no endpoint may violate: it must not raise, and its body must
survive the strict JSON encoder Starlette actually uses.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your broker + LLM keys (see SETUP.md)
python main.py dashboard      # → http://127.0.0.1:8000
```

Full instructions (web **and** Android build) are in **[SETUP.md](SETUP.md)**.

## License

See [LICENSE](LICENSE).
