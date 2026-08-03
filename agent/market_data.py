import datetime
import re
from typing import Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd
import requests


TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/india/scan"
DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"


def to_tradingview_symbol(ticker: str) -> str:
    """Convert an NSE ticker to TradingView symbol format, e.g. RELIANCE -> NSE:RELIANCE."""
    t = str(ticker or "").strip().upper()
    aliases = {
        "^NSEI": "NSE:NIFTY",
        "^INDIAVIX": "NSE:INDIAVIX",
        "L&T": "LT",
        "L&T.NS": "LT",
        "ZOMATO": "ETERNAL",
        "ZOMATO.NS": "ETERNAL",
    }
    t = aliases.get(t, t)
    if not t:
        return t
    if ":" in t:
        return t
    t = re.sub(r"\.(NS|BO)$", "", t)
    return f"NSE:{t}"


def plain_ticker(symbol: str) -> str:
    return str(symbol or "").split(":")[-1].replace(".NS", "").upper()


def scalar(value, default: float = 0.0) -> float:
    """Return a plain float from pandas/numpy scalar or one-cell Series/DataFrame."""
    try:
        if isinstance(value, pd.DataFrame):
            value = value.squeeze()
        if isinstance(value, pd.Series):
            if value.empty:
                return default
            value = value.iloc[0]
        if isinstance(value, np.ndarray):
            if value.size == 0:
                return default
            value = value.reshape(-1)[0]
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def tradingview_scan(tickers: Sequence[str], columns: Sequence[str]) -> Dict[str, dict]:
    """Fetch a TradingView scanner snapshot keyed by plain ticker."""
    tv_tickers = [to_tradingview_symbol(t) for t in tickers if str(t or "").strip()]
    if not tv_tickers:
        return {}
    payload = {
        "symbols": {"tickers": tv_tickers, "query": {"types": []}},
        "columns": list(columns),
    }
    try:
        resp = requests.post(TRADINGVIEW_SCAN_URL, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception:
        return {}

    out: Dict[str, dict] = {}
    for row in data:
        symbol = row.get("s", "")
        values = row.get("d") or []
        item = {col: values[i] if i < len(values) else None for i, col in enumerate(columns)}
        item["symbol"] = symbol
        out[plain_ticker(symbol)] = item
    return out


def tradingview_market_scan(
    columns: Sequence[str],
    limit: int = 100,
    types: Sequence[str] = ("stock", "fund"),
    sort_by: str = "relative_volume_10d_calc",
    sort_order: str = "desc",
) -> Dict[str, dict]:
    """Fetch a broad liquid India-market snapshot from TradingView."""
    payload = {
        "markets": ["india"],
        "symbols": {"query": {"types": list(types)}, "tickers": []},
        "columns": list(columns),
        "sort": {"sortBy": sort_by, "sortOrder": sort_order},
        "range": [0, max(1, int(limit))],
    }
    try:
        resp = requests.post(TRADINGVIEW_SCAN_URL, json=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except Exception:
        return {}

    out: Dict[str, dict] = {}
    for row in data:
        symbol = row.get("s", "")
        values = row.get("d") or []
        item = {col: values[i] if i < len(values) else None for i, col in enumerate(columns)}
        item["symbol"] = symbol
        out[plain_ticker(symbol)] = item
    return out


def tradingview_quote(ticker: str, columns: Sequence[str]) -> dict:
    return tradingview_scan([ticker], columns).get(plain_ticker(ticker), {})


def dhan_intraday_candles(
    security_id: str,
    access_token: str,
    from_dt: datetime.datetime,
    to_dt: datetime.datetime,
    interval: int = 5,
    exchange_segment: str = "NSE_EQ",
    instrument: str = "EQUITY",
) -> pd.DataFrame:
    """Fetch Dhan intraday OHLCV candles and return a timestamp-indexed DataFrame."""
    if not security_id or not access_token or str(access_token).startswith("YOUR"):
        return pd.DataFrame()

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": instrument,
        "interval": str(interval),
        "oi": False,
        "fromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": access_token,
    }
    try:
        resp = requests.post(DHAN_INTRADAY_URL, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        timestamps = data.get("timestamp") or []
        if not timestamps:
            return pd.DataFrame()
        df = pd.DataFrame({
            "open": data.get("open") or [],
            "high": data.get("high") or [],
            "low": data.get("low") or [],
            "close": data.get("close") or [],
            "volume": data.get("volume") or [],
        })
        if len(df) != len(timestamps):
            return pd.DataFrame()
        df.index = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["open", "high", "low", "close"])
    except Exception:
        return pd.DataFrame()


def quiet_download(symbols, *args, **kwargs):
    """
    Compatibility shim for older downloader-style calls.
    Returns the latest TradingView OHLCV snapshot as a DataFrame.
    """
    columns = ["open", "high", "low", "close", "volume"]
    if isinstance(symbols, (list, tuple, set)):
        tickers = list(symbols)
        rows = tradingview_scan(tickers, columns)
        idx = pd.DatetimeIndex([pd.Timestamp.utcnow()])
        close_data = {
            plain_ticker(t): rows.get(plain_ticker(t), {}).get("close", np.nan)
            for t in tickers
        }
        return pd.concat({"Close": pd.DataFrame(close_data, index=idx)}, axis=1)

    row = tradingview_quote(str(symbols), columns)
    if not row:
        return pd.DataFrame()
    idx = pd.DatetimeIndex([pd.Timestamp.utcnow()])
    return pd.DataFrame(
        {
            "Open": [row.get("open")],
            "High": [row.get("high")],
            "Low": [row.get("low")],
            "Close": [row.get("close")],
            "Volume": [row.get("volume")],
        },
        index=idx,
    )
