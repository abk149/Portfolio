# Setup & Installation

Two ways to run it: the **Python / web** stack, and the **Android app** (which
bundles the same Python backend and runs it on‑device).

---

## 1. Python / Web

### Prerequisites
- **Python 3.11 or 3.12** (3.13/3.14 can lag on scientific wheels).
- macOS/Linux/WSL. On Apple Silicon: `brew install python@3.12`.

### Install
```bash
git clone https://github.com/abk149/Portfolio.git
cd Portfolio

python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill it in (see "Configuration" below)
```

### Run
```bash
# Web dashboard — the easiest way to use everything
python main.py dashboard                 # → http://127.0.0.1:8000

# …or the CLI (Typer):
python main.py portfolio report
python main.py portfolio optimize --mode max_sharpe
python main.py screener scan --universe nifty50 --tech-min 65 --fund-min 55
python main.py intraday analyze --days 30
python main.py quant run                 # DR‑Quant funnel
python main.py --help                    # full command list
```

---

## 2. Configuration (`.env`)

Copy `.env.example` → `.env` and fill in only what you need. **Never commit
`.env`** (it's git‑ignored).

### Broker (pick one)
`BROKER=upstox` **or** `BROKER=groww`.

**Upstox** — create an app at <https://account.upstox.com/developer/apps>:
```
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URI=http://localhost:8000/callback   # must match the app EXACTLY
```
Then authenticate: open the dashboard → login, or `python -m src.upstox.auth`.
Upstox tokens are daily; the login flow re‑issues them.

**Groww** — Trading API (<https://groww.in/trade-api/docs>). Either paste a daily
token, or (recommended) set the **TOTP** secret once and it self‑mints daily:
```
GROWW_API_KEY=...
GROWW_TOTP_SECRET=...     # the base32 seed from "Generate TOTP token"
# or: GROWW_ACCESS_TOKEN=...   (a pasted daily token)
```
> Groww's live‑data feed needs an active data subscription. Without it, the app
> transparently falls back to a free public price source, so P&L and analytics
> still work.

### LLM (for agents, DR‑Quant, Macro Ideas, deep dive)
```
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=...        # get one at https://build.nvidia.com
```
Optional providers/fallbacks (Ollama / Anthropic) are documented inline in
`.env.example`.

### Optional
Telegram notifications, TimescaleDB logging, and risk‑free rate are all
optional — see `.env.example` for the full annotated list.

---

## 3. Android app

The Android app runs the **entire Python backend on the phone** via
[Chaquopy]. A Gradle task (`syncPythonSource`) copies `src/`, `config/`, and your
`.env` into the APK at build time — so the phone runs the same code as the desktop.

### Prerequisites
- **Android Studio** (latest) with **JDK 17**.
- Android SDK; a device or emulator on **API 24+** (arm64‑v8a).
- A working `.env` at the repo root (it gets bundled at build time).

### Build & run
```bash
# from the repo root
cd android
./gradlew assembleDebug          # or open the `android/` folder in Android Studio and Run
```
- First build is slow — Chaquopy downloads the Python runtime and wheels.
- If installation times out, `Build ▸ Clean Project`, uninstall the old app, and re‑run.

### Using the app
1. Open **⚙ Settings** → pick your broker, enter credentials + LLM key → Save.
2. Open the **System Terminal** (top bar) → **Start backend**; wait for it to go green.
3. **Login** (top‑bar 🔒) → Upstox (browser flow) or Groww (TOTP).
4. Explore: **Portfolio**, **Ideas** (macro), **DR‑Quant**, **U‑Map**,
   **Analysis** (Optimize / Performance / Screener / KB), and the **AI chat** (💬).

[Chaquopy]: https://chaquo.com/chaquopy/

---

## Notes
- This is a research/educational project — **not investment advice**.
- Keys and tokens live only in your local `.env` / device settings, never in git.
- Market‑data and LLM calls require internet; broker data requires a funded,
  API‑enabled broker account.
