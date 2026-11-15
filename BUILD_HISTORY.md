# Build history & engineering decision log

> Companion to the raw AI conversations (linked in the covering email). This is the curated narrative; the chats are the evidence.

## Iteration 1 - Product & architecture (ChatGPT, earlier session)
- Read the ENAM brief + reference doc. Decided the product is a **triage inbox**, not a dashboard.
- Changed an earlier recommendation (React + FastAPI) to **Python + Streamlit** because of the 15 Nov deadline: one codebase, faster to deploy and debug, still mobile-first via CSS.
- Chose real data (yfinance), honest data-status labels, CSV/link filing ingestion, retrieval-grounded research.

## Iteration 2 - Rebuild against the full brief (Claude, 14 Nov 2026)
First scaffold only covered radar + a thin filing list + PDF Q&A. Gaps vs the brief, and what I did about each:

| Brief asks for | Gap in scaffold | Change |
|---|---|---|
| Windowed digest, routine collapsed | single feed | 24h/3d/7d/custom window, Act/Watch/Noise tiers, noise collapsed |
| Triggered alerts, configurable | radar only | user-set volume×/price% rules + category alerts + scheduled Telegram job |
| Results → Revenue/EBITDA/PBT/PAT QoQ/YoY | missing | filing-table parser (row = [cur, QoQ ref, YoY ref]) + Yahoo quarterly fallback |
| Mgmt change → background | missing | name/role extraction, profile paragraph from filing, LinkedIn search link |
| 8 research views + promised-vs-delivered | generic chunk search | 8 section prompts + guidance-vs-actuals pipeline |
| Efficiency bonus | none | cheap/strong routing, disk cache, token meter, free extractive mode |
| Generalise to any ticker | upload only | screener.in document discovery for any NSE ticker |

## Iteration 3 - Verification (what actually happened)
- Wrote 22 offline unit tests (signal maths, 17-category classifier, scoring, results parsing, YoY date-matching, retrieval, abstention). All pass.
- Smoke-ran the whole UI against a stub Streamlit + synthetic prices. **This caught a real bug:** `row.flags` on a pandas row returns pandas' own `.flags` attribute, not my column → crash. Fixed with `row["flags"]`. (Lesson: avoid attribute access on DataFrame rows for generic names.)
- **Not yet verified (to be completed by the author - log results below):** live yfinance fetch, live NSE API, screener.in scraping, an LLM-backed run, Streamlit Cloud deployment, phone rendering.

## Iteration 4 - To fill in after you run it (keep this honest)
Record each of these as: *what I tried → what happened → what I changed.*
- [ ] Radar with live Yahoo data (any tickers failing? stale labels correct?)
- [ ] NSE live feed vs CSV upload (did NSE block you?)
- [ ] 3 real results filings: did the parser get Revenue/PBT/PAT right? Where did it fail?
- [ ] Research on TITAN / DIXON / one ticker not in the demo set; at least 2 questions the documents cannot answer (should abstain)
- [ ] Promised-vs-delivered on one company, sanity-checked by hand against the transcripts
- [ ] Phone test of the deployed link
- [ ] Token/cost footer after a full session

## Decisions I'd defend in an interview
1. Rules for triage, LLM only where reasoning is the product.
2. Retrieval-first so citations are structural, not generated.
3. Show the score arithmetic and the calculation strings - an investor should never have to trust a black box.
4. Say "unavailable" rather than invent data; label the one synthetic feed loudly.


## AI Build History

**AI Development Assistant:** ChatGPT (OpenAI)

ChatGPT was used throughout the FINN SignalDesk development lifecycle for:

1. Project planning and architecture decisions.
2. Python implementation and debugging support.
3. Test development and validation.
4. GitHub version control and deployment.
5. Streamlit deployment troubleshooting and verification.

### ChatGPT Conversation Reference

**FINN SignalDesk — Development, Testing and Deployment**

https://chatgpt.com/share/6af9ca68-1630-83e8-9fd4-e37ed4eb4022

This conversation documents the development workflow,
testing process, GitHub setup and deployment steps.

