"""Pull-side research engine: primary documents in -> cited, source-grounded analysis out.

Pipeline: find docs (screener.in or upload) -> page-aware text -> chunk -> TF-IDF index
-> retrieve top passages per question -> (optional) LLM answer constrained to those passages.
Primary sources only: annual reports + concall transcripts. No news, no broker notes.
Works for ANY NSE ticker for which screener.in lists documents (or the user uploads PDFs).
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
from pydoc import text
import re

import numpy as np
import requests

from . import config, llm

DOC_CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache" / "docs"


# ------------------------------------------------------------ doc discovery
def screener_docs(symbol: str):
    """Scrape annual-report + concall-transcript links from screener.in. Returns (docs, status)."""
    from bs4 import BeautifulSoup
    html = None
    for suffix in ("consolidated/", ""):
        try:
            r = requests.get(f"https://www.screener.in/company/{symbol}/{suffix}", headers=config.UA, timeout=20)
            if r.status_code == 200 and "documents" in r.text.lower():
                html = r.text
                break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    if html is None:
        return [], "screener.in unreachable or ticker not found - upload PDFs instead"
    soup = BeautifulSoup(html, "html.parser")
    docs, seen = [], set()
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.screener.in" + href
        if not href.startswith("http") or href in seen:
            continue
        low = txt.lower()
        if low.startswith("financial year"):
            docs.append({"kind": "annual_report", "label": f"Annual Report {txt.replace('Financial Year', 'FY').strip()}", "url": href})
            seen.add(href)
        elif low == "transcript":
            li = a.find_parent("li")
            m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d\d",
                          li.get_text(" ", strip=True) if li else "")
            docs.append({"kind": "concall", "label": f"Concall {m.group(0) if m else '(date n/a)'}", "url": href})
            seen.add(href)
    return docs, ("ok" if docs else "no documents listed on screener.in for this ticker")


def download_doc(url: str):
    """Download a doc once, cache on disk. Returns (bytes, content_type)."""
    DOC_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    f, meta = DOC_CACHE / f"{key}.bin", DOC_CACHE / f"{key}.type"
    if f.exists():
        return f.read_bytes(), meta.read_text() if meta.exists() else "application/pdf"
    r = requests.get(url, headers=config.UA, timeout=60, stream=True)
    r.raise_for_status()
    data = r.content
    if len(data) > 60 * 1024 * 1024:
        raise ValueError("document larger than 60 MB - skipped")
    ctype = r.headers.get("content-type", "application/pdf")
    f.write_bytes(data)
    meta.write_text(ctype)
    return data, ctype


def extract_pages(data: bytes, ctype: str = "application/pdf", max_pages: int = 400):
    """-> list of (page_no, text). Handles PDF and HTML transcripts."""
    if "html" in ctype.lower() or data[:5].lower().startswith(b"<!doc") or data[:5].lower().startswith(b"<html"):
        from bs4 import BeautifulSoup
        text = BeautifulSoup(data, "html.parser").get_text("\n")
        return [(1, re.sub(r"\n{3,}", "\n\n", text))]
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(data))
    pages = []
    for i in range(min(len(rd.pages), max_pages)):
        try:
            pages.append((i + 1, rd.pages[i].extract_text() or ""))
        except Exception:  # noqa: BLE001 - one bad page must not kill the document
            pages.append((i + 1, ""))
    return pages


# ------------------------------------------------------------- index / chunks
def chunk_pages(pages, doc_label: str, kind: str, size: int = 900, overlap: int = 150):
    chunks = []
    for pno, text in pages:
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        if len(text) < 60:
            continue
        start = 0
        while start < len(text):
            piece = text[start:start + size].strip()
            if len(piece) > 60:
                chunks.append({"doc": doc_label, "kind": kind, "page": pno, "text": piece})
            if start + size >= len(text):
                break
            start += size - overlap
    return chunks


class Index:
    def __init__(self, chunks):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.chunks = chunks
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        self.X = self.vec.fit_transform([c["text"] for c in chunks]) if chunks else None

    def search(self, query: str, k: int = 6, kinds=None, min_score: float = 0.05):
        if not self.chunks:
            return []
        qv = self.vec.transform([query])
        sims = (self.X @ qv.T).toarray().ravel()
        if kinds:
            mask = np.array([c["kind"] in kinds for c in self.chunks])
            sims = np.where(mask, sims, -1.0)
        order = np.argsort(-sims)
        out, seen = [], set()
        for i in order:
            if sims[i] < min_score:
                break
            c = self.chunks[i]
            key = (c["doc"], c["page"], c["text"][:60])
            if key in seen:
                continue
            seen.add(key)
            out.append((float(sims[i]), c))
            if len(out) >= k:
                break
        return out


def cite(c) -> str:
    return f"[{c['doc']} p.{c['page']}]"

def citations_valid(text: str, hits) -> bool:
    """Check that an LLM answer contains citations from the retrieved passages."""
    if not text or not text.strip():
        return False

    if text.strip() == "Not found in the provided documents.":
        return True

    allowed = {cite(chunk) for _, chunk in hits}
    found = set(re.findall(r"\[[^\[\]\n]+ p\.\d+\]", text))

    return bool(found) and found.issubset(allowed)


# -------------------------------------------------------------- the menu
SECTIONS = {
    "business": dict(title="Business snapshot", tier="cheap", kinds=["annual_report"],
                     query="business overview segments revenue mix products services customers geography market position",
                     ask="What the company does, its segments and revenue mix."),
    "financial": dict(title="Financial snapshot", tier="cheap", kinds=["annual_report", "concall"],
                      query="revenue EBITDA margin profit after tax earnings per share return on capital employed ROCE ROE dividend",
                      ask="Current headline numbers: revenue, margins, PAT and key ratios."),
    "trajectory": dict(title="Trajectory", tier="cheap", kinds=["annual_report", "concall"],
                       query="growth CAGR year on year segment growth margin expansion compression five year trend",
                       ask="Multi-year direction: what is growing and what is compressing."),
    "balance_sheet": dict(title="Balance sheet", tier="cheap", kinds=["annual_report"],
                          query="borrowings debt net debt debt to equity leverage working capital inventory receivables payables days liquidity",
                          ask="Debt, leverage, working-capital cycle, funding structure."),
    "cash": dict(title="Cash quality", tier="cheap", kinds=["annual_report", "concall"],
                 query="cash flow from operations free cash flow capex cash conversion working capital outflow",
                 ask="Cash-flow trend and whether profits convert to cash."),
    "narrative": dict(title="Narrative vs numbers", tier="strong", kinds=["annual_report", "concall"],
                      query="management outlook strategy growth margins challenges demand pressure headwinds confident",
                      ask="Where management's story and the financials agree or diverge."),
    "guidance": dict(title="Guidance", tier="cheap", kinds=["concall", "annual_report"],
                     query="guidance expect target aim outlook going forward next year growth margin capex FY",
                     ask="What management has explicitly guided on growth, margins, capex."),
    "bull_bear": dict(title="Bull vs bear", tier="strong", kinds=["annual_report", "concall"],
                      query="growth opportunity strength competitive advantage risk concern headwind debt dependence regulatory",
                      ask="A tight case each way, from the documents only."),
}

SYSTEM = (
    "You are FINN, a research analyst for a public-markets investment firm. Use ONLY the numbered source "
    "passages (and the STRUCTURED NUMBERS block, if present) supplied in the prompt. Never use outside "
    "knowledge of the company, news, or broker views. Every factual claim must end with its citation in the "
    "form [Doc p.N] copied from the passage header. If the passages do not support an answer, write exactly "
    "'Not found in the provided documents.' Be terse: short bullets, numbers over adjectives, no filler, "
    "no investment advice."
)


def _passages_block(hits):
    return "\n\n".join(f"### {cite(c)}\n{c['text']}" for _, c in hits)


def numbers_block(quarterly) -> str:
    if quarterly is None or getattr(quarterly, "empty", True):
        return ""
    q = quarterly.copy()
    q.index = [d.strftime("%b-%Y") for d in q.index]
    return ("STRUCTURED NUMBERS (₹ crore, quarterly; Yahoo Finance standardisation of exchange filings):\n"
            + q.round(1).to_string())


def run_section(index: Index, key: str, quarterly=None, symbol: str = "", k: int = 7):
    """Return dict(text, hits, mode). Falls back to extractive evidence when no LLM key / on error."""
    s = SECTIONS[key]
    hits = index.search(
    s["query"],
    k=k,
    kinds=s["kinds"]
)
    if not hits:
        return {"text": "Not found in the provided documents.", "hits": [], "mode": "none"}
    if not llm.available():
        return {"text": None, "hits": hits, "mode": "extractive"}
    extra = ""
    if key in ("narrative", "bull_bear", "financial", "trajectory", "cash"):
        extra = numbers_block(quarterly)
    task = {
        "business": "Summarise the business: what it does, segments, and revenue mix, in <=6 bullets.",
        "financial": "Give the headline financial snapshot (revenue, margins, PAT, key ratios) in <=6 bullets with figures.",
        "trajectory": "Describe the multi-year trajectory: what is growing, what is compressing. <=6 bullets with figures.",
        "balance_sheet": "Assess debt, leverage, working-capital cycle and funding structure. <=6 bullets with figures.",
        "cash": "Assess cash quality: operating cash flow trend and conversion of profit into cash. Flag red flags. <=6 bullets.",
        "narrative": ("Compare what management SAYS (outlook, tone, claims) with what the NUMBERS show. Produce a "
                      "3-column markdown table: Management says | Numbers show | Verdict (Agree/Diverge/Unclear). <=6 rows."),
        "guidance": "List every explicit forward guidance item (metric, target, horizon). Markdown table: Metric | Guidance | Source.",
        "bull_bear": "Give a tight bull case (3 bullets) and bear case (3 bullets), each grounded in the passages.",
    }[key]
    prompt = f"Company: {symbol}\nTask: {task}\n\n{extra}\n\nSOURCE PASSAGES:\n{_passages_block(hits)}"
    try:
        text = llm.complete(prompt, SYSTEM, tier=s["tier"], max_tokens=700)

        if not citations_valid(text, hits):
            return {
                "text": None,
                "hits": hits,
                "mode": "extractive (citation validation failed)"
        }

        return {"text": text, "hits": hits, "mode": f"llm-{s['tier']}"}
    except Exception as e:  # noqa: BLE001
        return {"text": None, "hits": hits, "mode": f"extractive (LLM error: {type(e).__name__})"}


def answer_question(index: Index, question: str, symbol: str = "", quarterly=None, k: int = 7):
    """Free-form Q&A. Abstains when retrieval finds nothing relevant - the 'unanswerable question' test."""
    hits = index.search(question, k=k, min_score=0.08)
    if not hits:
        return {"text": "Not found in the provided documents.", "hits": [], "mode": "abstained"}
    if not llm.available():
        return {"text": None, "hits": hits, "mode": "extractive"}
    prompt = (f"Company: {symbol}\nQuestion: {question}\n\n{numbers_block(quarterly)}\n\n"
              f"SOURCE PASSAGES:\n{_passages_block(hits)}\n\nAnswer in <=120 words with citations.")
    try:
        text = llm.complete(prompt, SYSTEM, tier="cheap", max_tokens=500)

        if not citations_valid(text, hits):
            return {
                "text": None,
                "hits": hits,
                "mode": "extractive (citation validation failed)"
        }

        return {"text": text, "hits": hits, "mode": "llm-cheap"}
    except Exception as e:  # noqa: BLE001
        return {"text": None, "hits": hits, "mode": f"extractive (LLM error: {type(e).__name__})"}


# ---------------------------------------------------- promised vs delivered
_GUIDE_RX = re.compile(r"\b(guid\w*|expect\w*|target\w*|aim\w*|aspir\w*|outlook|going forward|next (?:year|quarter)|"
                       r"fy ?\d{2}|confident)\b", re.I)


def extractive_guidance(index: Index, limit: int = 12):
    """Sentences from concalls that contain guidance language AND a number. Pure retrieval, no LLM."""
    out = []
    for c in index.chunks:
        if c["kind"] != "concall":
            continue
        for sent in re.split(r"(?<=[.?!])\s+", c["text"]):
            if _GUIDE_RX.search(sent) and re.search(r"\d", sent) and 40 < len(sent) < 320:
                out.append({"doc": c["doc"], "page": c["page"], "sentence": sent.strip()})
    # prefer sentences with % or growth/margin words
    out.sort(key=lambda d: -(("%" in d["sentence"]) * 2 + bool(re.search(r"growth|margin|capex|revenue", d["sentence"], re.I))))
    return out[:limit]


def promised_vs_delivered(index: Index, quarterly, symbol: str, call_docs: list):
    """Strong-tier synthesis: guidance extracted per call (cheap tier) vs actual quarterly numbers."""
    if not llm.available():
        return {"mode": "extractive", "guidance": extractive_guidance(index), "text": None}
    items = []
    for label in call_docs[:4]:
        chunks = [c for c in index.chunks if c["doc"] == label]
        # restrict retrieval to THIS call so each call's guidance stays attributable to its date
        sub = Index(chunks) if chunks else None
        hits = sub.search("guidance expect target outlook growth margin capex next year FY", k=5) if sub else []
        if not hits:
            continue
        prompt = ("Extract EXPLICIT forward guidance from these passages as a JSON list of "
                  '{"metric":"","guidance":"","page":0}. Only items with a stated number or direction. '
                  "Return [] if none.\n\n" + _passages_block(hits))
        try:
            raw = llm.complete(prompt, SYSTEM, tier="cheap", max_tokens=500)
        except Exception:  # noqa: BLE001
            continue
        items.append(f"## {label}\n{raw}")
    if not items:
        return {"mode": "extractive", "guidance": extractive_guidance(index), "text": None}
    prompt = ("Below is the guidance management gave on each earnings call, followed by actual quarterly results.\n"
              "Produce a markdown table: Call | Promised | Delivered (use the numbers) | Verdict "
              "(Delivered / Missed / Mixed / Can't verify). Be strict; if the numbers can't confirm it, say Can't verify.\n\n"
              + "\n\n".join(items) + "\n\n" + numbers_block(quarterly))
    try:
        text = llm.complete(prompt, SYSTEM, tier="strong", max_tokens=900)
        return {"mode": "llm-strong", "guidance": [], "text": text}
    except Exception as e:  # noqa: BLE001
        return {"mode": f"extractive (LLM error: {type(e).__name__})", "guidance": extractive_guidance(index), "text": None}
