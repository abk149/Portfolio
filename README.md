# Upstox Portfolio Intelligence

Modular Python toolkit that connects to your Upstox account to:

1. **Manage your portfolio** — fetch holdings/positions/orders, compute live P&L, allocation, exposure, and generate reports (CSV / HTML).
2. **Screen stocks** — combined technical (RSI, MACD, EMA crossovers, ATR, volume) + fundamental (P/E, ROE, debt/equity, growth) scoring with buy/hold/avoid recommendations.
3. **Analyze intraday trades** — read your historical intraday trades, compute win-rate, R-multiples, time-of-day patterns, mistakes; and scan the market for live intraday opportunities (gap-ups, ORB breakouts, momentum, volume surges, VWAP plays).
4. **Agentic layer** — three Claude-powered agents (Portfolio, Screener, Intraday) that orchestrate the underlying tools and produce natural-language insights.

## Quick start

### Mac mini M4 setup (Python version matters)

Python 3.14 is too new for some scientific wheels. On M-series Macs:

```bash
brew install python@3.12
python3.12 -m venv .venv && source .venv/bin/activate
```

### Install

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in UPSTOX_API_KEY / SECRET / REDIRECT_URI and ANTHROPIC_API_KEY
python -m src.upstox.auth   # one-time login → caches access token

# 🖥️  Dashboard — the easiest way to use everything
python main.py dashboard                    # → http://127.0.0.1:8000

# 🧠  D-R1-Quant — local-LLM funnel (DeepSeek-R1 via Ollama)
brew install ollama && ollama serve &
ollama pull deepseek-r1:8b
# set LLM_PROVIDER=ollama in .env, then:
python main.py quant macro                  # India VIX / PCR / USDINR → market mode
python main.py quant scan-technical         # Stage-1 TA-Lib screen
python main.py quant run                    # full Stage 1 → 4 funnel
python main.py quant schedule               # APScheduler on IST market hours
python main.py quant init-db                # apply TimescaleDB schema (optional)

# Portfolio
python main.py portfolio report
python main.py portfolio optimize --mode max_sharpe          # MPT max return/risk
python main.py portfolio optimize --mode target_return --target 0.20
python main.py portfolio optimize --include-buylist all_nse  # mix in screener buys
python main.py portfolio frontier                            # sample efficient frontier

# Screener (two-stage funnel: technical → fundamental on survivors)
python main.py screener refresh-instruments                  # pull Upstox master once
python main.py screener scan --universe all_nse --tech-min 65 --fund-min 55
python main.py screener technical --universe all_nse --tech-min 70   # stage-1 only
python main.py screener scan --universe nifty50              # curated small universe

# Intraday
python main.py intraday analyze --days 30
python main.py intraday scan

# Agents
python main.py agent portfolio "Optimize my portfolio for best return per risk and tell me what to buy/sell."

# Telegram — auth + report delivery
# 1. Create a bot with @BotFather, set TELEGRAM_BOT_TOKEN in .env
# 2. Either add your numeric chat_id to TELEGRAM_ALLOWED_CHAT_IDS, or set
#    TELEGRAM_AUTH_SECRET and /auth <secret> the bot from your phone.
python main.py telegram authorize 123456789           # whitelist a chat id
python main.py telegram send-report                    # push portfolio xlsx to all authorized chats
python main.py telegram send "Daily check-in"
python main.py telegram bot                            # run long-polling command bot
```

## Module map

| Path | Responsibility |
| ---- | -------------- |
| `src/upstox/` | Auth, REST client, data models |
| `src/data/` | Market data (Upstox + yfinance fallback), caching |
| `src/portfolio/` | Holdings/positions manager, P&L, allocation, reports |
| `src/screener/` | Technical, fundamental, combined scoring engine |
| `src/intraday/` | Historical trade analyzer + live opportunity scanner |
| `src/agents/` | Claude-powered orchestration agents with tool-use |
| `src/utils/` | Indicators, logging, formatting |
| `main.py` | CLI entrypoint (Typer) |

Every module is independent — you can import and use any piece without the agents or CLI.
