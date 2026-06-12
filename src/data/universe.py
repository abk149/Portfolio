"""Pre-defined symbol universes for the screener.

Maps human label → (yfinance ticker, NSE symbol, Upstox instrument_key).
Upstox instrument keys can be looked up from their daily instruments dump:
https://upstox.com/developer/api-documentation/instruments
For lightweight bootstrap we keep a small curated set here.
"""

NIFTY50 = [
    ("Reliance", "RELIANCE.NS", "RELIANCE", "NSE_EQ|INE002A01018"),
    ("HDFC Bank", "HDFCBANK.NS", "HDFCBANK", "NSE_EQ|INE040A01034"),
    ("ICICI Bank", "ICICIBANK.NS", "ICICIBANK", "NSE_EQ|INE090A01021"),
    ("Infosys", "INFY.NS", "INFY", "NSE_EQ|INE009A01021"),
    ("TCS", "TCS.NS", "TCS", "NSE_EQ|INE467B01029"),
    ("Bharti Airtel", "BHARTIARTL.NS", "BHARTIARTL", "NSE_EQ|INE397D01024"),
    ("ITC", "ITC.NS", "ITC", "NSE_EQ|INE154A01025"),
    ("L&T", "LT.NS", "LT", "NSE_EQ|INE018A01030"),
    ("HUL", "HINDUNILVR.NS", "HINDUNILVR", "NSE_EQ|INE030A01027"),
    ("Kotak Bank", "KOTAKBANK.NS", "KOTAKBANK", "NSE_EQ|INE237A01028"),
    ("SBIN", "SBIN.NS", "SBIN", "NSE_EQ|INE062A01020"),
    ("Axis Bank", "AXISBANK.NS", "AXISBANK", "NSE_EQ|INE238A01034"),
    ("Bajaj Finance", "BAJFINANCE.NS", "BAJFINANCE", "NSE_EQ|INE296A01024"),
    ("Asian Paints", "ASIANPAINT.NS", "ASIANPAINT", "NSE_EQ|INE021A01026"),
    ("Maruti", "MARUTI.NS", "MARUTI", "NSE_EQ|INE585B01010"),
    ("Sun Pharma", "SUNPHARMA.NS", "SUNPHARMA", "NSE_EQ|INE044A01036"),
    ("Titan", "TITAN.NS", "TITAN", "NSE_EQ|INE280A01028"),
    ("NTPC", "NTPC.NS", "NTPC", "NSE_EQ|INE733E01010"),
    ("UltraTech", "ULTRACEMCO.NS", "ULTRACEMCO", "NSE_EQ|INE481G01011"),
    ("Wipro", "WIPRO.NS", "WIPRO", "NSE_EQ|INE075A01022"),
]


UNIVERSES = {
    "nifty50": NIFTY50,
}


def resolve(name: str):
    if name not in UNIVERSES:
        raise KeyError(f"Unknown universe '{name}'. Available: {list(UNIVERSES)}")
    return UNIVERSES[name]
