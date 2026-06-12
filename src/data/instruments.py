"""Full Upstox instrument master.

Upstox publishes a daily-refreshed gzipped CSV of every tradable instrument.
We fetch NSE/BSE equities, cache to disk for 24h, and expose helpers to
resolve tickers across Upstox / yfinance.

Source: https://upstox.com/developer/api-documentation/instruments
"""
from __future__ import annotations

import gzip
import io
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from config import settings
from src.utils.logger import get_logger

log = get_logger("instruments")

URLS = {
    "NSE": "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz",
    "BSE": "https://assets.upstox.com/market-quote/instruments/exchange/BSE.csv.gz",
    "complete": "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
}


def _download(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    with gzip.open(io.BytesIO(r.content), "rt") as fh:
        return pd.read_csv(fh)


def load_instruments(exchange: str = "NSE", refresh: bool = False) -> pd.DataFrame:
    """Return the full instrument dump (cached on disk for 24h)."""
    cache_file = settings.cache_dir / f"instruments_{exchange}.parquet"
    if not refresh and cache_file.exists():
        age_h = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(cache_file)

    log.info(f"Fetching Upstox instruments for {exchange} ...")
    df = _download(URLS[exchange])
    df.to_parquet(cache_file, index=False)
    log.info(f"Cached {len(df):,} instruments → {cache_file.name}")
    return df


# Patterns that almost certainly indicate NON-equity instruments slipping
# through Upstox's master dump (NCDs, sovereign gold bonds, mutual funds,
# REITs/InvITs, govt securities, etc.).
_NON_EQUITY_NAME_PATTERNS = (
    # Bonds / debt — these strings really only appear in bond product names
    "NCD", "BOND", "DEBENTURE", "TAX FREE", "TAX-FREE",
    "SGB", "SOVEREIGN GOLD",
    "G-SEC", "GSEC", "T-BILL",
    # Funds
    "MUTUAL FUND",
    "INDEX FUND", "INDEXFUND",
    "FUND OF FUND",
    # ETFs
    "ETF", "EXCHANGE TRADED",
    # REITs / InvITs
    "INVIT", "REIT",
    "REAL ESTATE INVESTMENT TRUST",
    "INFRASTRUCTURE INVESTMENT TRUST",
    # Pref / warrants
    "PREFERENCE SHARE", "PREF SHARES",
    "WARRANT",
    # Note: deliberately REMOVED these — they were dropping legitimate
    # equities as substrings:
    #   "TRUST" (matches "INFOSYS TRUST", "TRUST FINTECH" etc.)
    #   "MF " (matches "PFC LTD" if followed by space — risky)
    #   "GOI " (similar)
    #   "TREASURY" (could match "TREASURY CORP")
    #   "MAMC" / "NETF" / "FOF" (substring ambiguity)
    #   "NIFTY 50" (some legit names mention it)
    #   "BEES" (covered by symbol denylist + endswith)
)


# Hard symbol denylist — common ETF tickers that slip through name filters
_NON_EQUITY_SYMBOLS = {
    "LIQUIDBEES", "GOLDBEES", "SILVERBEES", "NIFTYBEES", "JUNIORBEES",
    "BANKBEES", "PSUBNKBEES", "ITBEES", "PHARMABEES", "AUTOBEES",
    "SILVER1", "SILVER360", "GOLD1", "GOLD360",
    "MIDCAP", "SMALLCAP", "MIDCAPIETF", "MID150", "SMLCAP",
    "LIQUIDETF", "LIQUIDCASE", "LIQUIDIETF",
}


def equities(
    exchange: str = "NSE",
    series: tuple[str, ...] = ("EQ",),
    refresh: bool = False,
) -> pd.DataFrame:
    """Filter the instrument master to **pure cash equities only**.

    Excludes: bonds, NCDs, SGBs, mutual funds, ETFs, REITs/InvITs, warrants,
    govt securities, anything whose tradingsymbol starts with a digit (those
    are almost always bond series tags like "812REC27" or "763GS2031").
    """
    df = load_instruments(exchange, refresh=refresh)

    rename = {"trading_symbol": "tradingsymbol", "exchange_token": "token"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    mask = pd.Series(True, index=df.index)

    # 1. Segment must be cash equity
    if "segment" in df.columns:
        mask &= df["segment"].isin([f"{exchange}_EQ"])

    # 2. Instrument-type must be equity
    if "instrument_type" in df.columns:
        mask &= df["instrument_type"].isin(["EQ", "EQUITY"])

    # 3. Series filter (EQ = main board cash equity)
    if "series" in df.columns:
        mask &= df["series"].isin(series)

    # 4. Trading symbol must start with a LETTER (drops "812REC27", "763GS31", …)
    if "tradingsymbol" in df.columns:
        sym = df["tradingsymbol"].astype(str)
        mask &= sym.str.match(r"^[A-Za-z]")
        # Bond suffixes commonly seen
        mask &= ~sym.str.contains(r"-N[0-9]+$|-W[0-9]+$|-RE$|-BE$", regex=True, na=False)

    # 5. Name doesn't contain bond / fund / govt-sec / ETF keywords
    if "name" in df.columns:
        upname = df["name"].astype(str).str.upper()
        for pat in _NON_EQUITY_NAME_PATTERNS:
            mask &= ~upname.str.contains(pat, regex=False, na=False)

        # 5b. ETF / index-fund naming convention — these are listed under
        # NSE series "EQ" but are NOT companies. Their names look like
        # "EDELAMC - EGOLD", "TATAAML-TATAGOLD", "AXISMF - AXISNIFTY".
        # The tell-tale is an AMC/AML/MF token immediately before a dash.
        # A real company ("HDFC ASSET MANAGEMENT COMPANY LIMITED") has no
        # such pattern, so this is precise.
        mask &= ~upname.str.contains(r"AM[CL]\s*-", regex=True, na=False)
        mask &= ~upname.str.contains(r"\bMF\s*-", regex=True, na=False)
        mask &= ~upname.str.contains(r"-\s*[A-Z]*GOLD\b", regex=True, na=False)
        mask &= ~upname.str.contains(r"-\s*[A-Z]*SILVER\b", regex=True, na=False)

    # 6. Symbol-level ETF/fund filters
    if "tradingsymbol" in df.columns:
        upsym = df["tradingsymbol"].astype(str).str.upper()
        mask &= ~upsym.isin(_NON_EQUITY_SYMBOLS)
        mask &= ~upsym.str.endswith("BEES")          # Nippon ETFs
        # ETF/index-fund symbol SUFFIXES (anchored — won't hit GOLDIAM etc.)
        mask &= ~upsym.str.contains(
            r"(?:ETF|IETF|GETF|CASE|GSEC|BND)$", regex=True, na=False,
        )
        # Symbols that are exactly an index/commodity ETF token
        mask &= ~upsym.str.fullmatch(
            r"(?:[EM]?GOLD|[EM]?SILVER|GOLD\d*|SILVER\d*|NIFTY\w*|SENSEX\w*)",
            na=False,
        )

    out = df[mask].copy()

    yf_suffix = ".NS" if exchange == "NSE" else ".BO"
    out["yf_ticker"] = (out["tradingsymbol"].astype(str)
                       .str.replace("-EQ$", "", regex=True) + yf_suffix)

    keep = [c for c in [
        "instrument_key", "tradingsymbol", "name", "isin", "lot_size",
        "tick_size", "yf_ticker", "segment",
    ] if c in out.columns]
    return out[keep].reset_index(drop=True)


def resolve_universe(name: str) -> list[tuple[str, str, str, str]]:
    """Resolve a universe name to (display, yf_ticker, nse_sym, instrument_key) tuples.

    Supports:
      - 'nifty50' (curated, in src/data/universe.py)
      - 'all_nse', 'all_bse', 'all'  → every cash equity from the Upstox dump
    """
    name = name.lower()
    if name == "all_nse":
        eq = equities("NSE")
    elif name == "all_bse":
        eq = equities("BSE")
    elif name == "all":
        eq = pd.concat([equities("NSE"), equities("BSE")], ignore_index=True)
    else:
        from src.data.universe import resolve as _r
        return _r(name)

    # Drop blacklisted instruments so we don't waste API calls
    from src.data.instrument_blacklist import is_blacklisted
    eq = eq[~eq["instrument_key"].apply(is_blacklisted)]

    return list(zip(eq["name"].fillna(eq["tradingsymbol"]), eq["yf_ticker"],
                    eq["tradingsymbol"], eq["instrument_key"]))


_RESOLVE_CACHE: dict[str, str] = {}        # normalised_symbol → instrument_key


def _norm(s: str) -> str:
    """Normalise a ticker for lookup. Strips '.NS' / '.BO', '-EQ', whitespace."""
    if not s:
        return ""
    s = str(s).strip().upper()
    for suffix in (".NS", ".BO", "-EQ", "-BL"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _build_resolve_cache() -> None:
    """Index NSE then BSE equities by trading symbol → instrument_key.
    NSE wins on conflicts (the typical case for retail traders)."""
    global _RESOLVE_CACHE
    if _RESOLVE_CACHE:
        return
    cache: dict[str, str] = {}
    for ex in ("BSE", "NSE"):    # NSE last → NSE overwrites BSE
        try:
            df = equities(ex)
        except Exception:
            continue
        for _, r in df.iterrows():
            sym = _norm(r.get("tradingsymbol", ""))
            ikey = r.get("instrument_key", "")
            if sym and ikey:
                cache[sym] = ikey
            # ISIN fallback
            isin = r.get("isin")
            if isin:
                cache[str(isin).upper()] = ikey
    _RESOLVE_CACHE = cache


def resolve_instrument_key(symbol_or_ticker: str) -> Optional[str]:
    """Look up an Upstox instrument_key from a trading symbol, yf ticker, or ISIN.

    Examples:
        resolve_instrument_key("RELIANCE")        → "NSE_EQ|INE002A01018"
        resolve_instrument_key("RELIANCE.NS")     → "NSE_EQ|INE002A01018"
        resolve_instrument_key("INE002A01018")    → "NSE_EQ|INE002A01018"

    Returns None if no match.
    """
    if not symbol_or_ticker:
        return None
    _build_resolve_cache()
    return _RESOLVE_CACHE.get(_norm(symbol_or_ticker))


def search(query: str, exchange: str = "NSE", limit: int = 20) -> pd.DataFrame:
    eq = equities(exchange)
    q = query.upper()
    mask = (
        eq["tradingsymbol"].astype(str).str.upper().str.contains(q)
        | eq["name"].astype(str).str.upper().str.contains(q)
    )
    return eq[mask].head(limit)
