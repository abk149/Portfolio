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
  scored stock universe.
- **Universe Map** — a crawler that scores the whole universe (technical +
  fundamental) into a persistent knowledge base, visualized as a
  tech‑vs‑fundamental scatter.
- **Macro Ideas** — ingests **recent, date‑filtered** signals from ~20 sources
  (financial‑news RSS, Google News, Reddit) + a live macro snapshot (VIX / PCR /
  USDINR), then an LLM weighs **all** factors into one *holistic* view and
  returns 3–7 conviction‑ranked picks — each with sector, a **quant entry
  price**, and a detailed multi‑factor thesis.
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
| `main.py` | Typer CLI entrypoint |

Every module is independent — import and use any piece without the CLI or agents.

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
