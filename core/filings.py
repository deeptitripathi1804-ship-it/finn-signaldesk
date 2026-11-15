"""Corporate announcements: ingestion (NSE / CSV / demo), classification, prioritisation.

Design: a transparent rule-based classifier (free, instant, auditable) over the 17
ENAM categories, then an additive priority score whose every point is explained to
the user. An LLM is NOT needed for triage - it is reserved for optional one-liners.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import time
from urllib.parse import quote_plus

import pandas as pd
import requests

from . import config

# ------------------------------------------------------------ classification
# Order matters: first match wins. Specific / high-signal patterns come first.
_RULES = [
    ("unclassified", r"\bauditors?\b|secretarial audit|scrutini[sz]er|\besop\b|stock option|employee stock|"
                     r"trading window|newspaper (publication|advertisement)|duplicate share|loss of share|"
                     r"\bbrsr\b|\besg\b|sustainability report|shareholding pattern|compliance certificate|"
                     r"certificate under regulation|reconciliation of share capital|statement of investor complaints"),
    ("earnings_call", r"earnings call|conference call|concall|analyst call|call transcript|transcript of|"
                      r"audio (recording|link)|investor call"),
    ("credit_rating", r"credit rating|\bcrisil\b|\bicra\b|care ratings|india ratings|rating (upgrade|downgrade|reaffirm|assigned)"),
    ("pledging", r"\bpledge|invocation of|encumbrance|release of pledge"),
    ("insider_trading", r"regulation 7\(2\)|regulation 29|prohibition of insider trading|"
                        r"(acquisition|disposal|sale|purchase) of (equity )?shares by (promoter|director|designated)|"
                        r"disclosure under regulation 31"),
    ("results", r"financial results?|quarterly results?|unaudited (standalone|consolidated)|audited (standalone|consolidated)|"
                r"results for the (quarter|year|half)|board meeting.{0,80}(consider|approv).{0,60}results|"
                r"investor presentation|earnings (release|presentation)|results presentation|press release.{0,40}results"),
    ("management_change", r"resign|cessation|appointment of|re-?appointment|change in (director|management|key managerial)|"
                          r"\bceo\b|\bcfo\b|chief (executive|financial|operating) officer|managing director|"
                          r"whole[- ]time director|company secretary|senior management|key managerial"),
    ("M&A", r"acqui(re|sition)|merger|amalgamation|demerger|scheme of arrangement|joint venture|"
            r"incorporation of (a )?(wholly|subsidiary|step)|subsidiary|divest|slump sale|stake (in|sale)|strategic investment"),
    ("fund_raise", r"rights issue|\bqip\b|qualified institutions? placement|preferential (issue|allotment)|buy-?back|"
                   r"commercial paper|non-convertible debenture|\bncds?\b|fund[- ]?raising|raising of funds|"
                   r"\bfccb\b|convertible warrants|public issue|\bipo\b|\bofs\b|offer for sale"),
    ("litigation", r"litigation|lawsuit|court|tribunal|\bnclt\b|\bnclat\b|penalty|show[- ]cause|\bsebi\b.{0,30}order|"
                   r"regulatory action|insolvency|\bcirp\b|(income[- ]tax|gst|customs).{0,30}(demand|order|notice)|"
                   r"search (and|&) seizure|enforcement directorate|\bcbi\b|fraud|default"),
    ("bonus_split", r"bonus (issue|shares)|stock split|sub-?division of (equity )?shares|split of (equity )?shares"),
    ("dividend", r"dividend|record date"),
    ("capex", r"\bcapex\b|capital expenditure|new (plant|facility|factory)|capacity expansion|greenfield|brownfield|"
              r"expansion of (plant|manufacturing|capacity)|setting up of (a )?(new )?(plant|facility|unit)"),
    ("agm_egm", r"annual general meeting|\bagm\b|extra-?ordinary general meeting|\begm\b|e-?voting|book closure"),
    ("postal_ballot", r"postal ballot"),
    ("investor_meetings", r"analyst.{0,15}(meet|visit)|investor meet|schedule of (analyst|investor)|"
                          r"institutional investor|investor conference|group meeting"),
    ("press_release", r"press release|order (win|received)|bagged|new order|letter of (award|intent)|launch(es|ed)?\b|"
                      r"partnership|\bmou\b|collaborat|awarded|clarification|commissioning|tie-?up"),
]
_COMPILED = [(c, re.compile(p, re.I)) for c, p in _RULES]

BASE_SCORE = {
    "results": 90, "M&A": 88, "management_change": 78, "litigation": 80, "fund_raise": 74,
    "credit_rating": 70, "pledging": 72, "capex": 62, "dividend": 58, "bonus_split": 58,
    "earnings_call": 48, "insider_trading": 46, "press_release": 40, "investor_meetings": 18,
    "agm_egm": 22, "postal_ballot": 20, "unclassified": 4,
}

# (pattern, points, label) - each fired boost is shown to the user as a "why"
_BOOSTS = [
    (re.compile(r"downgrad", re.I), 14, "rating downgrade language"),
    (re.compile(r"resign|cessation|steps? down", re.I), 8, "departure rather than appointment"),
    (re.compile(r"\bcfo\b|chief financial|\bceo\b|chief executive|managing director", re.I), 10, "top-of-house role"),
    (re.compile(r"fraud|default|insolven|\bcirp\b|show[- ]cause|search (and|&) seizure|enforcement", re.I), 12, "serious regulatory / credit language"),
    (re.compile(r"invocation", re.I), 12, "pledge invoked by lender"),
    (re.compile(r"\b(crore|cr\.?|billion|bn)\b", re.I), 3, "size disclosed in filing"),
    (re.compile(r"intimation|schedule|notice of", re.I), -12, "intimation/notice, not the outcome"),
]

TIERS = [(75, "Act now"), (45, "Watch"), (0, "Noise")]


def classify(text: str):
    """Return (category, matched_text)."""
    t = text or ""
    for cat, rx in _COMPILED:
        m = rx.search(t)
        if m:
            return cat, m.group(0)
    return "unclassified", ""


def score(category: str, text: str, move_pct: float | None = None,
          interests: set | None = None, price_threshold: float = 3.0):
    """Additive, explainable priority score -> (score 0-100, [reasons])."""
    s = BASE_SCORE.get(category, 10)
    reasons = [f"{category.replace('_', ' ')} base {BASE_SCORE.get(category, 10)}"]
    for rx, pts, label in _BOOSTS:
        if rx.search(text or ""):
            # departure/CFO boosts only make sense for people changes
            if label in ("departure rather than appointment", "top-of-house role") and category != "management_change":
                continue
            if label == "rating downgrade language" and category != "credit_rating":
                continue
            s += pts
            reasons.append(f"{pts:+d} {label}")
    if move_pct is not None and abs(move_pct) >= price_threshold and category not in ("unclassified",):
        s += 8
        reasons.append(f"+8 stock moved {move_pct:+.1f}% on the day")
    if interests and category in interests:
        s += 6
        reasons.append("+6 category you follow")
    s = max(0, min(100, s))
    return s, reasons


def tier_of(s: float) -> str:
    for cutoff, name in TIERS:
        if s >= cutoff:
            return name
    return "Noise"


def enrich(df: pd.DataFrame, moves: dict | None = None, interests: set | None = None,
           price_threshold: float = 3.0) -> pd.DataFrame:
    """Classify + score every row. Expects columns symbol, time, subject, detail, url."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "time", "subject", "detail", "url", "category",
                                     "score", "tier", "reasons", "matched"])
    out = df.copy()
    cats, scores, tiers, reasons, matched = [], [], [], [], []
    for _, r in out.iterrows():
        text = f"{r.get('subject', '')}. {r.get('detail', '')}"
        cat, m = classify(text)
        sc, rs = score(cat, text, (moves or {}).get(r["symbol"]), interests, price_threshold)
        cats.append(cat); scores.append(sc); tiers.append(tier_of(sc)); reasons.append(rs); matched.append(m)
    out["category"], out["score"], out["tier"], out["reasons"], out["matched"] = cats, scores, tiers, reasons, matched
    out = out.drop_duplicates(subset=["symbol", "subject", "time"])
    return out.sort_values(["score", "time"], ascending=[False, False]).reset_index(drop=True)


# ------------------------------------------------------------------ sources
def _parse_time(x):
    return pd.to_datetime(x, dayfirst=True, errors="coerce")


def fetch_nse_one(session: requests.Session, symbol: str, d_from: dt.date, d_to: dt.date):
    url = ("https://www.nseindia.com/api/corporate-announcements?index=equities"
           f"&symbol={quote_plus(symbol)}&from_date={d_from:%d-%m-%Y}&to_date={d_to:%d-%m-%Y}")
    r = session.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    rows = []
    for it in data if isinstance(data, list) else []:
        rows.append({
            "symbol": symbol,
            "time": _parse_time(it.get("an_dt") or it.get("sort_date")),
            "subject": (it.get("desc") or "").strip(),
            "detail": (it.get("attchmntText") or "").strip(),
            "url": it.get("attchmntFile") or "",
        })
    return rows


def fetch_nse_many(symbols, d_from: dt.date, d_to: dt.date):
    """Best-effort NSE pull. Returns (df, status, errors). NSE often blocks cloud IPs -
    when that happens the caller falls back to CSV/demo and SAYS SO."""
    s = requests.Session()
    s.headers.update({**config.UA, "Accept": "application/json, text/plain, */*",
                      "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
    errors, rows = {}, []
    try:
        s.get("https://www.nseindia.com/", timeout=15)  # primes the cookies NSE requires
    except Exception as e:  # noqa: BLE001
        return pd.DataFrame(), "failed", {"nse": f"{type(e).__name__}: {e}"}
    for sym in symbols:
        try:
            rows += fetch_nse_one(s, sym, d_from, d_to)
        except Exception as e:  # noqa: BLE001
            errors[sym] = f"{type(e).__name__}: {e}"
        time.sleep(0.25)  # be polite; NSE rate-limits aggressively
    df = pd.DataFrame(rows)
    if df.empty:
        return df, "failed", errors
    status = "live" if len(errors) < len(symbols) / 2 else "partial"
    return df.dropna(subset=["time"]), status, errors


def load_csv(file_or_text) -> pd.DataFrame:
    """Import exchange filings from CSV. Flexible headers (NSE/BSE downloads work as-is)."""
    if hasattr(file_or_text, "read"):
        raw = file_or_text.read()
        raw = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    else:
        raw = file_or_text
    df = pd.read_csv(io.StringIO(raw))
    lower = {c.lower().strip(): c for c in df.columns}

    def pick(*names):
        for n in names:
            for k, orig in lower.items():
                if n in k:
                    return orig
        return None

    cols = {
        "symbol": pick("symbol", "scrip", "ticker"),
        "time": pick("broadcast", "an_dt", "date", "time"),
        "subject": pick("subject", "desc", "headline", "category"),
        "detail": pick("details", "attchmnttext", "text", "particulars"),
        "url": pick("attachment", "url", "link", "file"),
    }
    if not cols["symbol"] or not cols["time"] or not cols["subject"]:
        raise ValueError("CSV needs at least symbol, date/time and subject columns")
    out = pd.DataFrame({
        "symbol": df[cols["symbol"]].astype(str).str.upper().str.strip(),
        "time": df[cols["time"]].map(_parse_time),
        "subject": df[cols["subject"]].astype(str),
        "detail": df[cols["detail"]].astype(str) if cols["detail"] else "",
        "url": df[cols["url"]].astype(str) if cols["url"] else "",
    })
    return out.dropna(subset=["time"])


def demo_filings(symbols, now: dt.datetime | None = None) -> pd.DataFrame:
    """ILLUSTRATIVE rows used only when live feeds are unreachable. Every subject carries a
    [DEMO] prefix and the UI labels the whole feed as sample data - they are NOT real filings."""
    now = now or dt.datetime.now()
    T = [  # (symbol, hours_ago, subject, detail)
        ("TITAN", 2, "Outcome of Board Meeting - Unaudited Financial Results for the quarter ended September 30",
         "Board approved unaudited standalone and consolidated financial results. Revenue from operations 14,500.00 12,900.00 11,800.00"),
        ("DIXON", 3, "Appointment of Chief Financial Officer",
         "Board approved appointment of Mr. A. Sample as Chief Financial Officer (KMP). Brief profile: 20+ years in finance across electronics and consumer durables; previously Group Controller at a listed manufacturer; Chartered Accountant."),
        ("HDFCBANK", 5, "Credit rating reaffirmed by CRISIL", "CRISIL reaffirmed AAA/Stable on long-term borrowings programme."),
        ("PERSISTENT", 7, "Acquisition of 100% stake in a US-based digital engineering firm",
         "Board approved acquisition for consideration of USD 85 million subject to customary approvals."),
        ("ITC", 9, "Intimation of Record Date for payment of dividend", "Interim dividend record date announced."),
        ("RELIANCE", 11, "Press Release - New order received", "Company has bagged a large order for green energy equipment."),
        ("TRENT", 14, "Resignation of Chief Executive Officer",
         "Mr. B. Sample has tendered resignation as CEO citing personal reasons, effective end of next month."),
        ("CDSL", 16, "Disclosure under Regulation 7(2) - acquisition by promoter", "Promoter acquired shares through open market."),
        ("POLYCAB", 20, "Capital expenditure - approval of new facility",
         "Board approved capex of Rs 600 crore for a greenfield cable plant."),
        ("LT", 22, "Schedule of Analyst / Institutional Investor Meetings", "Meetings with institutional investors scheduled."),
        ("MARUTI", 26, "Notice of Postal Ballot", "Postal ballot notice dispatched."),
        ("BAJFINANCE", 30, "Raising of funds through Non-Convertible Debentures", "Board approved issue of NCDs aggregating Rs 5,000 crore."),
        ("SUNPHARMA", 33, "Receipt of order from regulatory authority - show cause notice",
         "Company received a show cause notice from a regulatory authority; evaluating response."),
        ("ASTRAL", 40, "Loss of Share Certificate - Duplicate share certificate issued", "Routine."),
        ("APOLLOHOSP", 44, "Allotment of equity shares under ESOP", "ESOP allotment, routine."),
        ("ICICIBANK", 52, "Transcript of earnings call", "Transcript of Q2 earnings call uploaded."),
        ("TCS", 60, "Newspaper publication - financial results", "Newspaper advertisement."),
        ("DMART", 70, "Trading Window Closure", "Trading window closed for designated persons."),
    ]
    rows = [{
        "symbol": s, "time": now - dt.timedelta(hours=h), "subject": "[DEMO] " + subj,
        "detail": det, "url": "",
    } for s, h, subj, det in T if s in symbols]
    return pd.DataFrame(rows)


# ------------------------------------------------- management-change helpers
_NAME_RX = re.compile(
    r"\b(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+([A-Z][\w.\-']+(?:\s+[A-Z][\w.\-']+){0,3})")


def person_from_filing(text: str):
    """Pull (name, role) from the announcement text. Role = first matched top-level title."""
    name = None
    m = _NAME_RX.search(text or "")
    if m:
        name = m.group(0).strip().rstrip(".")
    role_m = re.search(r"chief (?:executive|financial|operating|technology|risk|human)[\w ]{0,15}officer|"
                       r"managing director|whole[- ]time director|independent director|company secretary|\bCEO\b|\bCFO\b|\bCOO\b",
                       text or "", re.I)
    role = role_m.group(0) if role_m else None
    return name, role


def linkedin_search_url(name: str, company: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{name} {company}")


def extract_profile_snippet(text: str, max_chars: int = 700) -> str | None:
    """Find the 'brief profile / background' passage that appointment filings usually carry."""
    if not text:
        return None
    m = re.search(r"(brief (profile|background|resume)|profile|background|experience)[:\-\s]", text, re.I)
    if not m:
        return None
    snippet = re.sub(r"\s+", " ", text[m.start(): m.start() + max_chars]).strip()
    return snippet
