"""Quarterly results: first-cut headline numbers (Revenue, EBITDA, PBT, PAT) with QoQ / YoY.

Two paths, both transparent about their source:
  1. Parse the exchange results filing itself (PDF text): Indian results tables list
     [current quarter, previous quarter, same quarter last year] side by side, so one
     row gives QoQ and YoY with no extra data.
  2. Fallback: Yahoo Finance quarterly statements (standardised from the same filings).
"""
from __future__ import annotations

import io
import re

import pandas as pd

# ----------------------------------------------------------- common helpers
def pct_change(cur, ref):
    if cur is None or ref is None or ref == 0 or pd.isna(cur) or pd.isna(ref):
        return None
    return (cur - ref) / abs(ref) * 100.0


def _to_num(tok: str):
    neg = tok.startswith("(") and tok.endswith(")")
    t = tok.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


# ---------------------------------------------------- path 1: filing PDF text
_LABELS = {
    "revenue": re.compile(r"(total )?revenue from operations|net sales|income from operations", re.I),
    "pbt": re.compile(r"profit\s*(/\s*\(?loss\)?\s*)?before\s+tax", re.I),
    "pat": re.compile(r"net profit|profit (/\s*\(?loss\)?\s*)?for the (period|quarter|year)|profit after tax", re.I),
    "finance": re.compile(r"finance costs?", re.I),
    "dep": re.compile(r"depreciation", re.I),
}
_NUM = re.compile(r"\(?-?\d[\d,]*\.?\d*\)?")


def _row_numbers(line: str, label_rx):
    m = label_rx.search(line)
    if not m:
        return None
    rest = line[m.end():]
    nums = [_to_num(t) for t in _NUM.findall(rest)]
    nums = [n for n in nums if n is not None]
    return nums[:3] if len(nums) >= 2 else None


def detect_unit(text: str) -> str:
    t = text.lower()
    if re.search(r"(rs\.?|₹|inr)?\s*in\s+crore|crores?", t):
        return "₹ crore"
    if re.search(r"in\s+lakh|lakhs?", t):
        return "₹ lakh"
    if re.search(r"in\s+million|millions?", t):
        return "₹ million"
    return "as reported"


def parse_results_text(text: str) -> dict:
    """First-cut extraction from results-filing text.

    Returns {'unit', 'rows': {metric: [cur, qoq_ref, yoy_ref]}, 'notes': [...]}.
    EBITDA is DERIVED (PBT + finance costs + depreciation) because filings don't print it,
    and the derivation is stated so the investor can see it - it includes other income.
    """
    rows, notes = {}, []
    lines = [l for l in text.splitlines() if l.strip()]
    for line in lines:
        for key, rx in _LABELS.items():
            if key in rows:
                continue
            if key == "pbt" and re.search(r"exceptional", line, re.I):
                continue  # want the final PBT, not "before exceptional items"
            nums = _row_numbers(line, rx)
            if nums:
                rows[key] = nums
    # EBITDA derivation
    if all(k in rows for k in ("pbt", "finance", "dep")):
        n = min(len(rows["pbt"]), len(rows["finance"]), len(rows["dep"]))
        rows["ebitda"] = [rows["pbt"][i] + rows["finance"][i] + rows["dep"][i] for i in range(n)]
        notes.append("EBITDA derived = PBT + finance costs + depreciation & amortisation (includes other income). "
                     "Not meaningful for banks/NBFCs.")
    else:
        notes.append("EBITDA could not be derived from this filing (missing finance cost or depreciation row).")
    for k in ("revenue", "pbt", "pat"):
        if k not in rows:
            notes.append(f"{k.upper()} row not found - filing layout not recognised; open the filing to verify.")
    notes.append("First cut from text extraction - verify against the filing before acting.")
    return {"unit": detect_unit(text), "rows": rows, "notes": notes}


def table_from_parsed(parsed: dict) -> pd.DataFrame:
    recs = []
    for key, label in (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")):
        v = parsed["rows"].get(key)
        if not v:
            recs.append({"Metric": label, "This qtr": None, "Prev qtr": None, "Year-ago qtr": None,
                         "QoQ %": None, "YoY %": None})
            continue
        cur = v[0]
        qoq_ref = v[1] if len(v) > 1 else None
        yoy_ref = v[2] if len(v) > 2 else None
        recs.append({"Metric": label, "This qtr": cur, "Prev qtr": qoq_ref, "Year-ago qtr": yoy_ref,
                     "QoQ %": pct_change(cur, qoq_ref), "YoY %": pct_change(cur, yoy_ref)})
    return pd.DataFrame(recs)


def pdf_bytes_to_text(data: bytes, max_pages: int = 12) -> str:
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(data))
    return "\n".join((rd.pages[i].extract_text() or "") for i in range(min(len(rd.pages), max_pages)))


# ------------------------------------------------ path 2: Yahoo quarterly data
_YF_ROWS = {
    "Revenue": ["Total Revenue", "Operating Revenue"],
    "EBITDA": ["EBITDA", "Normalized EBITDA"],
    "PBT": ["Pretax Income"],
    "PAT": ["Net Income", "Net Income Common Stockholders"],
}


def quarterly_from_stmt(stmt: pd.DataFrame) -> pd.DataFrame:
    """Yahoo quarterly_income_stmt (rows=items, cols=dates) -> tidy df indexed by quarter-end, ₹ crore."""
    if stmt is None or stmt.empty:
        return pd.DataFrame()
    out = {}
    for metric, names in _YF_ROWS.items():
        for n in names:
            if n in stmt.index:
                out[metric] = stmt.loc[n].astype(float) / 1e7  # rupees -> crore
                break
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def changes_from_quarterly(q: pd.DataFrame) -> dict:
    """Latest quarter vs previous quarter and vs same quarter a year earlier (matched BY DATE,
    not by position, so a missing quarter can't silently produce a wrong YoY)."""
    if q is None or q.empty:
        return {"status": "no data"}
    q = q.sort_index()
    latest = q.index[-1]
    prev = q.index[-2] if len(q) > 1 and 60 <= (latest - q.index[-2]).days <= 120 else None
    yoy = None
    for d in q.index[:-1]:
        if 340 <= (latest - d).days <= 390:
            yoy = d
    recs = []
    for m in q.columns:
        cur = q.at[latest, m]
        qref = q.at[prev, m] if prev is not None else None
        yref = q.at[yoy, m] if yoy is not None else None
        recs.append({"Metric": m, "This qtr": cur, "Prev qtr": qref, "Year-ago qtr": yref,
                     "QoQ %": pct_change(cur, qref), "YoY %": pct_change(cur, yref)})
    return {"status": "ok", "latest": latest, "prev": prev, "yoy": yoy, "table": pd.DataFrame(recs)}


def fetch_yahoo_quarterly(symbol: str) -> pd.DataFrame:
    import yfinance as yf
    t = yf.Ticker(f"{symbol}.NS")
    return quarterly_from_stmt(t.quarterly_income_stmt)
