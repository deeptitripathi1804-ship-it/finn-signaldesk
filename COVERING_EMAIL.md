**To:** assessment@enam.com
**Subject:** IIT Delhi – AI Engineer - 2027

Hello ENAM team,

Please find my submission for the FINN build challenge.

**Prototype (open on a phone):** <DEPLOYED STREAMLIT LINK>
**Repository:** <GITHUB LINK>
**AI build history:** <CHATGPT SHARE LINK> · <CLAUDE CHAT LINK / EXPORT> (curated narrative in `BUILD_HISTORY.md`)

**What I built.** FINN SignalDesk is a triage inbox for an investor, not a dashboard. *Push:* a windowed digest across an 18-stock portfolio that classifies every filing into your 17 categories and ranks it Act-now / Watch / Noise with the score arithmetic shown, results filings turned into Revenue/EBITDA/PBT/PAT QoQ and YoY, management changes shown with the background paragraph from the filing, user-set volume and price tripwires on end-of-day data, and a scheduled Telegram job for real phone alerts. *Pull:* research on any NSE ticker from annual reports and concall transcripts only, with the eight views, plus "promised vs delivered" (guidance from the last four calls against actual quarterly numbers). Every claim is cited to a document and page, and FINN answers "not found in the provided documents" when the sources can't support an answer.

**Key decisions and trade-offs.** Rules rather than an LLM for triage (free, auditable, testable). Retrieval before generation, so citations come from the retriever rather than the model. Cheap model for extraction, strong model only for narrative-vs-numbers, bull/bear and promised-vs-delivered; prompts capped at a handful of passages, cached on disk, token usage shown in-app; it also runs with no API key at all. Correctness details I tested: YoY matched by date not position, volume baseline excludes the current day, honest "unavailable" states instead of invented data.

**Deliberately left out.** Scaled NSE/BSE monitoring (endpoints are fragile; I support live NSE with CSV import as the dependable path), LinkedIn scraping (a search link instead), intraday data, portfolio P&L, OCR for scanned PDFs.

**Known limitations.** Results-PDF parsing is a labelled first cut and EBITDA is derived; NSE can block cloud IPs, in which case the app clearly labels a demo sample; TF-IDF retrieval can miss paraphrases. <ADD ANYTHING YOU FOUND WHEN TESTING>

**Next.** Embedding retrieval with reranking, BSE feed, XBRL parsing for exact results numbers, per-person background enrichment via a licensed profile API, watchlist-specific alert rules, and a voice/"morning brief" mode.

Thank you for your time.
<YOUR NAME>
