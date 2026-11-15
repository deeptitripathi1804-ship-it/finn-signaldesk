"""Static configuration: the mock portfolio, defaults and shared constants."""

# 18-stock mock portfolio: deliberately mixed across sectors and market-cap bands.
PORTFOLIO = [
    {"symbol": "RELIANCE",   "name": "Reliance Industries", "sector": "Energy & Conglomerate", "cap": "Large"},
    {"symbol": "TCS",        "name": "Tata Consultancy",    "sector": "IT Services",           "cap": "Large"},
    {"symbol": "HDFCBANK",   "name": "HDFC Bank",           "sector": "Banks",                 "cap": "Large"},
    {"symbol": "ICICIBANK",  "name": "ICICI Bank",          "sector": "Banks",                 "cap": "Large"},
    {"symbol": "SUNPHARMA",  "name": "Sun Pharma",          "sector": "Pharma",                "cap": "Large"},
    {"symbol": "MARUTI",     "name": "Maruti Suzuki",       "sector": "Auto",                  "cap": "Large"},
    {"symbol": "ITC",        "name": "ITC",                 "sector": "FMCG",                  "cap": "Large"},
    {"symbol": "LT",         "name": "Larsen & Toubro",     "sector": "Capital Goods",         "cap": "Large"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance",       "sector": "NBFC",                  "cap": "Large"},
    {"symbol": "TITAN",      "name": "Titan Company",       "sector": "Consumer",              "cap": "Large"},
    {"symbol": "DIXON",      "name": "Dixon Technologies",  "sector": "Electronics Mfg",       "cap": "Mid"},
    {"symbol": "POLYCAB",    "name": "Polycab India",       "sector": "Cables & Wires",        "cap": "Mid"},
    {"symbol": "PERSISTENT", "name": "Persistent Systems",  "sector": "IT Services",           "cap": "Mid"},
    {"symbol": "TRENT",      "name": "Trent",               "sector": "Retail",                "cap": "Mid"},
    {"symbol": "DMART",      "name": "Avenue Supermarts",   "sector": "Retail",                "cap": "Large"},
    {"symbol": "CDSL",       "name": "CDSL",                "sector": "Capital Markets",       "cap": "Small"},
    {"symbol": "ASTRAL",     "name": "Astral",              "sector": "Building Materials",    "cap": "Mid"},
    {"symbol": "APOLLOHOSP", "name": "Apollo Hospitals",    "sector": "Healthcare",            "cap": "Large"},
]

SYMBOLS = [p["symbol"] for p in PORTFOLIO]
META = {p["symbol"]: p for p in PORTFOLIO}

# Ad-hoc research demo set (the engine itself accepts ANY NSE ticker).
RESEARCH_DEMO = ["TITAN", "DIXON", "PERSISTENT", "CDSL", "ITC"]

# User-tunable defaults
DEFAULTS = {
    "vol_mult": 2.0,    # volume >= N x trailing average
    "price_pct": 3.0,   # |daily move| >= X %
    "lookback": 20,     # trailing sessions for the volume baseline
}

# Announcement categories (adapted from the ENAM reference document)
CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation",
    "insider_trading", "earnings_call", "investor_meetings", "credit_rating",
    "dividend", "bonus_split", "capex", "press_release", "postal_ballot",
    "agm_egm", "pledging", "unclassified",
]

# Categories pushed as triggered alerts by default
DEFAULT_ALERT_CATEGORIES = ["results", "M&A", "management_change", "fund_raise",
                            "litigation", "credit_rating", "pledging"]

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
