"""End-of-day price & volume: fetching (yfinance) and unusual-move detection.

All signal maths lives in pure functions so it can be unit-tested offline.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import pickle
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config

CACHE_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache" / "eod.pkl"


# ----------------------------------------------------------------- fetching
def _fetch_one(symbol: str, period: str):
    import yfinance as yf  # imported lazily so pure functions work without it

    t = yf.Ticker(f"{symbol}.NS")
    # auto_adjust=False -> raw close (split-adjusted only), matches what the exchange shows
    df = t.history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise ValueError("no rows returned")
    df = df[["Close", "Volume"]].dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def fetch_eod(symbols, period: str = "6mo"):
    """Return (frames, errors, fetched_at, status).

    status: 'live'   - fetched now from Yahoo Finance
            'cached' - Yahoo failed for everything; last good snapshot served
            'failed' - nothing available (the UI must say so; we never invent data)
    """
    frames, errors = {}, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {s: ex.submit(_fetch_one, s, period) for s in symbols}
        for s, f in futs.items():
            try:
                frames[s] = f.result()
            except Exception as e:  # noqa: BLE001 - report every failure verbatim
                errors[s] = f"{type(e).__name__}: {e}"
    fetched_at = dt.datetime.now()
    if frames:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "wb") as fh:
                pickle.dump({"frames": frames, "fetched_at": fetched_at}, fh)
        except Exception:  # noqa: BLE001
            pass
        return frames, errors, fetched_at, "live"
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "rb") as fh:
                blob = pickle.load(fh)
            return blob["frames"], errors, blob["fetched_at"], "cached"
        except Exception:  # noqa: BLE001
            pass
    return {}, errors, fetched_at, "failed"


# ------------------------------------------------------------- signal maths
def compute_signal(df: pd.DataFrame, vol_mult: float, price_pct: float, lookback: int = 20):
    """Compute the latest-session signal for one stock. Returns dict or None."""
    d = df.copy()
        # Ensure numeric values
    d["Close"] = pd.to_numeric(d["Close"], errors="coerce")
    d["Volume"] = pd.to_numeric(d["Volume"], errors="coerce")

    # Remove invalid observations
    d = d.dropna(subset=["Close", "Volume"])
    d = d[
        (d["Close"] > 0) &
        (d["Volume"] >= 0)
    ]

    # Ensure chronological order
    d = d.sort_index()

    # Remove duplicate trading dates
    d = d[~d.index.duplicated(keep="last")]

    if len(d) < lookback + 2:
        return None


    last, prev = d.iloc[-1], d.iloc[-2]
    pct = (last["Close"] / prev["Close"] - 1.0) * 100.0
    base = d["Volume"].iloc[-(lookback + 1):-1]  # trailing window EXCLUDING today
    avg_vol = float(base.mean())
    ratio = float(last["Volume"] / avg_vol) if avg_vol > 0 else float("nan")
    ret_5d = (last["Close"] / d["Close"].iloc[-6] - 1.0) * 100.0 if len(d) >= 6 else float("nan")

    flags = []
    if not np.isnan(ratio) and ratio >= vol_mult:
        flags.append(f"Volume {ratio:.1f}x its {lookback}-day average")
    if abs(pct) >= price_pct:
        flags.append(f"Price {pct:+.1f}% on the day")

    as_of = d.index[-1].date()
    age = (dt.date.today() - as_of).days
    return {
        "as_of": as_of,
        "close": float(last["Close"]),
        "prev_close": float(prev["Close"]),
        "pct": float(pct),
        "volume": float(last["Volume"]),
        "avg_volume": avg_vol,
        "vol_ratio": ratio,
        "ret_5d": float(ret_5d),
        "flags": flags,
        "flagged": bool(flags),
        "stale": age > 4,   # >4 calendar days covers weekends + one holiday
        "spark": [float(x) for x in d["Close"].iloc[-30:].tolist()],
        "calc": (f"pct = {last['Close']:.2f} / {prev['Close']:.2f} - 1; "
                 f"vol ratio = {last['Volume']:,.0f} / mean(prior {lookback} sessions) {avg_vol:,.0f}"),
    }


def build_radar(frames: dict, vol_mult: float, price_pct: float, lookback: int = 20) -> pd.DataFrame:
    rows = []
    for sym, df in frames.items():
        sig = compute_signal(df, vol_mult, price_pct, lookback)
        if sig is None:
            continue
        meta = config.META.get(sym, {"name": sym, "sector": "-", "cap": "-"})
        rows.append({"symbol": sym, "name": meta["name"], "sector": meta["sector"], **sig})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["abs_pct"] = out["pct"].abs()
    return out.sort_values(["flagged", "abs_pct"], ascending=[False, False]).reset_index(drop=True)


def move_map(radar: pd.DataFrame) -> dict:
    """symbol -> latest daily % move (used to boost filing priority when price confirms)."""
    if radar is None or radar.empty:
        return {}
    return dict(zip(radar["symbol"], radar["pct"]))
