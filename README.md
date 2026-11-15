# FINN SignalDesk
**Investment intelligence, minus the noise.** A mobile-first financial agent for ENAM's AI Engineer Build Challenge.

## The point of view
Most finance apps are dashboards: they show you everything. FINN is a **triage inbox**. It ranks what changed in *your* window into **Act now / Watch / Noise**, collapses the noise, and shows its working. The investor sets the tripwires; FINN watches them.

| Mode | Tab | What it does |
|---|---|---|
| **Push** | 📬 Brief | Filings across the 18-stock portfolio for a window you pick (24h / 3d / 7d / custom), classified into ENAM's 17 categories and ranked. Results filings → first-cut Revenue/EBITDA/PBT/PAT with QoQ & YoY. Management changes → who, role, and the background paragraph from the filing + LinkedIn search link. |
| **Push** | 📡 Radar | End-of-day volume and price tripwires with **user-set thresholds**, sparklines, and every calculation inspectable. |
| **Push** | `alerts_job.py` | Scheduled after-close job (GitHub Action) → Telegram phone notification. |
| **Pull** | 🔎 Ask FINN | Any NSE ticker. Pulls annual report + concall transcripts from screener.in (or your uploads), indexes them, and answers the 8 research views + **Promised vs delivered** - every claim cited `[Doc p.N]`; refuses when the documents can't answer. |

## Run it (Windows, VS Code terminal)
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```
Open http://localhost:8501 (on your phone, use the Network URL on the same Wi-Fi).

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && streamlit run app.py`

Optional AI layer: copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and add `ANTHROPIC_API_KEY` **or** `GEMINI_API_KEY`. No key = still works in extractive retrieval mode, displaying relevant source passages and page references instead of generated summaries.

## Deploy (free, ~5 min)
1. Push this folder to a **public GitHub repo**.
2. share.streamlit.io → *New app* → pick repo, main file `app.py`.
3. (Optional) *Advanced → Secrets* → paste your API key.
4. Open the URL on your phone and test. Submit that link.

## Architecture
```
app.py (Streamlit UI)  ── core/market.py     yfinance EOD → pure signal maths → radar
                       ── core/filings.py    NSE fetch | CSV | labelled demo → rule classifier → explainable score
                       ── core/results.py    filing-PDF table parse (QoQ/YoY in one row) | Yahoo quarterly fallback
                       ── core/research.py   screener.in docs → page-aware chunks → TF-IDF → cited answers
                       ── core/llm.py        cheap/strong routing · disk cache · token accounting (optional)
alerts_job.py + .github/workflows/alerts.yml  scheduled push
tests/test_core.py    27 offline tests
```

## Key decisions (and what they cost)
- **Rules before LLMs for triage.** Free, instant, auditable, testable. Every score point is shown to the user.
- **Retrieval before generation.** TF-IDF over page-tagged chunks: zero cost, and citations are structural (page numbers come from the retriever, not the model).
- **Model routing.** Cheap model: extraction/summaries. Strong model: only *narrative vs numbers*, *bull vs bear*, *promised vs delivered*. Prompts are capped at ~7 passages; identical calls are cached on disk; usage is displayed in the app footer.
- **Primary sources only on the pull side.** Prompts forbid outside knowledge; no news or broker data is touched.
- **Never fabricate.** Data unavailable → the UI says so (red chip). The only synthetic data is the *Demo sample* feed, whose every row is prefixed `[DEMO]` and banner-labelled; it exists so the product is demoable when NSE blocks cloud IPs.
- **Point-in-time correctness.** YoY is matched by *date*, not row position; the volume baseline *excludes* the current day; negative bases use |base| so growth from a loss isn't sign-flipped. Each has a test.

## Known limits (honest)
- NSE's announcement API often blocks cloud IPs; use **CSV upload** (NSE/BSE download) or run locally. BSE API not wired.
- Results parsing is a **first cut** from PDF text; layouts vary. EBITDA is *derived* (PBT + finance cost + depreciation, incl. other income) and labelled so; not meaningful for banks/NBFCs. Fallback is Yahoo's standardised quarterly statements (~4-5 quarters).
- LinkedIn isn't scraped (ToS); the app shows the profile paragraph from the filing and a search link.
- TF-IDF misses paraphrase-only matches; embeddings are the next upgrade. Scanned/image PDFs need OCR.
- Quarterly guidance-vs-actual works best with 3-4 calls loaded; transcripts must be text-extractable.
- Business snapshot and balance-sheet analysis require annual-report documents; concall-only knowledge bases may return no results for these views.

## Tests
`python -m unittest discover -s tests -v` (no network, no API key needed)
