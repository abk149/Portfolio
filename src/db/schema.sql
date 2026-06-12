-- D-R1-Quant TimescaleDB schema.
-- Run once:  psql "$TIMESCALE_DSN" -f src/db/schema.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Daily OHLCV (hypertable by ts)
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    instrument_key TEXT,
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    volume      DOUBLE PRECISION,
    PRIMARY KEY (ts, symbol)
);
SELECT create_hypertable('ohlcv_daily', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_symbol ON ohlcv_daily (symbol, ts DESC);

-- Fundamental snapshots
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol        TEXT NOT NULL,
    debt_to_equity        DOUBLE PRECISION,
    current_ratio         DOUBLE PRECISION,
    free_cash_flow_cr     DOUBLE PRECISION,
    promoter_pledging_pct DOUBLE PRECISION,
    revenue_growth_pct    DOUBLE PRECISION,
    earnings_growth_pct   DOUBLE PRECISION,
    pe                    DOUBLE PRECISION,
    roe                   DOUBLE PRECISION,
    source                TEXT,
    notes                 TEXT,
    PRIMARY KEY (ts, symbol)
);
SELECT create_hypertable('stock_fundamentals', 'ts', if_not_exists => TRUE);

-- Agent reasoning log (every <think> + decision)
CREATE TABLE IF NOT EXISTS agent_logs (
    ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id    TEXT NOT NULL,
    stage     TEXT,
    actor     TEXT,
    action    TEXT,
    symbol    TEXT,
    payload   JSONB,
    PRIMARY KEY (ts, run_id, stage, actor)
);
SELECT create_hypertable('agent_logs', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_agent_logs_run ON agent_logs (run_id, ts);

-- Macro snapshots (one row per intraday tick / daily check)
CREATE TABLE IF NOT EXISTS macro_snapshots (
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    india_vix     DOUBLE PRECISION,
    nifty_pcr     DOUBLE PRECISION,
    usdinr        DOUBLE PRECISION,
    nifty_chg_pct DOUBLE PRECISION,
    mode          TEXT,
    PRIMARY KEY (ts)
);
SELECT create_hypertable('macro_snapshots', 'ts', if_not_exists => TRUE);
