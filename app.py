"""FINN SignalDesk - investment intelligence, minus the noise.

Interaction model: a TRIAGE INBOX, not a dashboard.
  Brief  (push) : what changed in my window, ranked Act-now / Watch / Noise (noise is collapsed)
  Radar  (push) : my end-of-day tripwires - volume and price - with every calculation inspectable
  Ask FINN (pull): cited, primary-source research on ANY NSE ticker; it refuses when the documents can't answer
Every number carries a data-status label. If data can't be fetched, we say so; we never invent it.
"""
import datetime as dt
import html

import pandas as pd
import streamlit as st

from core import config, filings, llm, market, research, results

st.set_page_config(page_title="FINN SignalDesk", page_icon="📡", layout="centered",
                   initial_sidebar_state="collapsed")

# ----------------------------------------------------------------------- style
st.markdown("""
<style>
.block-container{padding-top:1.1rem;padding-bottom:4rem;max-width:760px}
html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.finn-h{display:flex;align-items:baseline;gap:.6rem;margin:0 0 .15rem 0}
.finn-h b{font-size:1.65rem;letter-spacing:-.02em}
.finn-h span{color:#5b6b7a;font-size:.9rem}
.chip{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.72rem;font-weight:600;margin:0 .3rem .25rem 0;border:1px solid #d8dee4;background:#f6f8fa;color:#3d4852}
.chip.live{background:#e6f6ee;border-color:#a8dcc0;color:#14693e}
.chip.warn{background:#fff4e0;border-color:#f0cf8f;color:#8a5a00}
.chip.bad{background:#fdeaea;border-color:#efb3b3;color:#a02020}
.chip.cat{background:#eef2ff;border-color:#c9d3fb;color:#2c3e91}
.card{border:1px solid #e3e8ee;border-left:5px solid #9aa7b4;border-radius:10px;padding:.65rem .8rem .55rem;margin:.55rem 0 .1rem;background:#fff}
.card.act{border-left-color:#d6336c}.card.watch{border-left-color:#e8a317}.card.noise{border-left-color:#c3ccd5}
.card .top{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.card .sym{font-weight:800;letter-spacing:.01em}
.card .when{margin-left:auto;color:#6b7785;font-size:.75rem}
.card .subj{margin-top:.25rem;font-size:.95rem;line-height:1.3;color:#1b2733}
.card .score{font-size:.72rem;color:#6b7785}
.rrow{display:flex;align-items:center;gap:.6rem;padding:.55rem .1rem;border-bottom:1px solid #eef1f4}
.rrow .l{flex:1.4;min-width:0}.rrow .l b{display:block}.rrow .l small{color:#6b7785}
.rrow .m{flex:1}.rrow .r{flex:1.1;text-align:right}
.pct{font-weight:700;font-variant-numeric:tabular-nums}.up{color:#14693e}.dn{color:#c0392b}
.flag{display:inline-block;font-size:.7rem;padding:.05rem .45rem;border-radius:6px;background:#fff0f4;color:#b3174f;margin-top:.15rem}
.stat{display:inline-block;margin-right:1rem}.stat b{font-size:1.15rem}.stat small{color:#6b7785;display:block}
div[data-testid="stExpander"] summary p{font-size:.85rem}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------- state
ss = st.session_state
ss.setdefault("vol_mult", config.DEFAULTS["vol_mult"])
ss.setdefault("price_pct", config.DEFAULTS["price_pct"])
ss.setdefault("interests", list(config.DEFAULT_ALERT_CATEGORIES))
ss.setdefault("kb", {})          # symbol -> {"index": Index, "docs": [...]}
ss.setdefault("outputs", [])     # research outputs, newest first
ss.setdefault("research_symbol", "TITAN")
ss.setdefault("res_cache", {})


def chip(text, kind=""):
    return f'<span class="chip {kind}">{html.escape(str(text))}</span>'


def fmt(v, nd=1):
    return "–" if v is None or pd.isna(v) else f"{v:,.{nd}f}"


# ---------------------------------------------------------------------- header
st.markdown('<div class="finn-h"><b>FINN</b><span>SignalDesk · investment intelligence, minus the noise</span></div>',
            unsafe_allow_html=True)

with st.expander("⚙️ My watch rules - I decide what 'unusual' means", expanded=False):
    c1, c2 = st.columns(2)
    ss["vol_mult"] = c1.slider("Volume ≥ N × average", 1.2, 6.0, float(ss["vol_mult"]), 0.1)
    ss["price_pct"] = c2.slider("Price move ≥ ±X %", 1.0, 10.0, float(ss["price_pct"]), 0.5)
    ss["interests"] = st.multiselect("Announcement types I want pushed to me", config.CATEGORIES, ss["interests"])
    ai_lines = st.toggle("AI one-line summaries on Act-now items (cheap model)", value=False,
                         disabled=not llm.available(),
                         help="Needs ANTHROPIC_API_KEY or GEMINI_API_KEY. Off by default to keep cost at zero.")
    if not llm.available():
        st.caption("No LLM key detected → app runs fully in free, extractive mode.")


# ------------------------------------------------------------------- data I/O
@st.cache_data(ttl=1800, show_spinner="Fetching end-of-day prices & volume…")
def load_eod(symbols):
    return market.fetch_eod(list(symbols))


@st.cache_data(ttl=900, show_spinner="Pulling announcements from NSE…")
def load_live_filings(symbols, d_from, d_to):
    return filings.fetch_nse_many(list(symbols), d_from, d_to)


@st.cache_data(ttl=3600, show_spinner=False)
def load_quarterly(symbol):
    try:
        return results.fetch_yahoo_quarterly(symbol), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


@st.cache_data(ttl=86400, show_spinner=False)
def load_pages(url):
    data, ctype = research.download_doc(url)
    return research.extract_pages(data, ctype)


@st.cache_data(ttl=3600, show_spinner=False)
def headline_numbers(symbol, url):
    """Filing-first, Yahoo-fallback. Returns dict(source, table, unit, notes) or dict(error)."""
    if url and url.lower().split("?")[0].endswith(".pdf"):
        try:
            data, _ = research.download_doc(url)
            parsed = results.parse_results_text(results.pdf_bytes_to_text(data))
            tbl = results.table_from_parsed(parsed)
            if tbl["This qtr"].notna().sum() >= 2:
                return {"source": "Parsed from the exchange results filing (first cut)", "table": tbl,
                        "unit": parsed["unit"], "notes": parsed["notes"]}
        except Exception:  # noqa: BLE001
            pass
    q, err = load_quarterly(symbol)
    if q is None or q.empty:
        return {"error": f"Quarterly data unavailable ({err or 'Yahoo returned nothing'}). Open the filing directly."}
    ch = results.changes_from_quarterly(q)
    if ch.get("status") != "ok":
        return {"error": "No quarterly data."}
    notes = [f"Latest quarter {ch['latest']:%b %Y}; QoQ vs {ch['prev']:%b %Y}" if ch["prev"] is not None else "QoQ reference quarter missing.",
             f"YoY vs {ch['yoy']:%b %Y}" if ch["yoy"] is not None else "YoY reference quarter not available from Yahoo (needs 5 quarters)."]
    return {"source": "Yahoo Finance quarterly statements (standardised from filings)", "table": ch["table"],
            "unit": "₹ crore", "notes": notes}


def show_headline(res):
    if "error" in res:
        st.warning(res["error"])
        return
    t = res["table"]
    st.dataframe(t.style.format({"This qtr": "{:,.1f}", "Prev qtr": "{:,.1f}", "Year-ago qtr": "{:,.1f}",
                                 "QoQ %": "{:+.1f}%", "YoY %": "{:+.1f}%"}, na_rep="–"),
                 hide_index=True, use_container_width=True)
    st.caption(f"{res['source']} · unit: {res['unit']} · " + " · ".join(res["notes"]))


# ------------------------------------------------------------------ EOD radar
symbols = tuple(config.SYMBOLS)
frames, eod_errors, eod_at, eod_status = load_eod(symbols)
radar = market.build_radar(frames, ss["vol_mult"], ss["price_pct"], config.DEFAULTS["lookback"])
moves = market.move_map(radar)

status_chips = ""
if eod_status == "live":
    status_chips += chip(f"Prices: LIVE Yahoo EOD · fetched {eod_at:%H:%M}", "live")
elif eod_status == "cached":
    status_chips += chip(f"Prices: CACHED snapshot from {eod_at:%d %b %H:%M} - Yahoo unreachable", "warn")
else:
    status_chips += chip("Prices: UNAVAILABLE - no data shown, none invented", "bad")

tab_brief, tab_radar, tab_ask = st.tabs(["📬 Brief", "📡 Radar", "🔎 Ask FINN"])

# ============================================================= BRIEF (push)
with tab_brief:
    c1, c2 = st.columns([3, 2])
    win = c1.radio("Window", ["24 hours", "3 days", "7 days", "Custom"], horizontal=True, label_visibility="collapsed")
    src = c2.selectbox("Source", ["Live NSE", "CSV upload", "Demo sample"], label_visibility="collapsed")
    now = dt.datetime.now()
    if win == "Custom":
        d1, d2 = st.columns(2)
        start = dt.datetime.combine(d1.date_input("From", now.date() - dt.timedelta(days=2)), dt.time.min)
        end = dt.datetime.combine(d2.date_input("To", now.date()), dt.time.max)
    else:
        start = now - {"24 hours": dt.timedelta(hours=24), "3 days": dt.timedelta(days=3), "7 days": dt.timedelta(days=7)}[win]
        end = now

    feed, feed_chip = pd.DataFrame(), ""
    if src == "Live NSE":
        df, st_, errs = load_live_filings(symbols, start.date(), end.date())
        if df is None or df.empty:
            st.warning("NSE did not return data (it frequently blocks cloud IPs). Showing the labelled DEMO sample "
                       "instead - switch Source to *CSV upload* to use real filings.")
            feed = filings.demo_filings(set(config.SYMBOLS))
            feed_chip = chip("Announcements: DEMO SAMPLE - illustrative, not real filings", "bad")
        else:
            feed = df
            feed_chip = chip(f"Announcements: LIVE NSE ({st_})", "live" if st_ == "live" else "warn")
    elif src == "CSV upload":
        up = st.file_uploader("NSE/BSE announcements CSV (symbol, date/time, subject, details, attachment link)", type="csv")
        if up is not None:
            try:
                feed = filings.load_csv(up)
                feed_chip = chip(f"Announcements: CSV import ({len(feed)} rows)", "live")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read CSV: {e}")
        else:
            st.info("Upload a CSV downloaded from NSE/BSE corporate announcements to see your real digest.")
    else:
        feed = filings.demo_filings(set(config.SYMBOLS))
        feed_chip = chip("Announcements: DEMO SAMPLE - illustrative, not real filings", "bad")

    st.markdown(status_chips + feed_chip, unsafe_allow_html=True)

    if not feed.empty:
        feed = feed[(feed["time"] >= start) & (feed["time"] <= end)]
    enriched = filings.enrich(feed, moves, set(ss["interests"]), ss["price_pct"])

    # ---- triggered alerts
    eod_alerts = radar[radar["flagged"]] if not radar.empty else pd.DataFrame()
    fil_alerts = enriched[(enriched["category"].isin(ss["interests"])) & (enriched["tier"] != "Noise")] if not enriched.empty else enriched
    n_act = int((enriched["tier"] == "Act now").sum()) if not enriched.empty else 0
    n_watch = int((enriched["tier"] == "Watch").sum()) if not enriched.empty else 0
    n_noise = int((enriched["tier"] == "Noise").sum()) if not enriched.empty else 0

    st.markdown(
        f'<span class="stat"><b>{len(eod_alerts)}</b><small>market tripwires</small></span>'
        f'<span class="stat"><b>{n_act}</b><small>act now</small></span>'
        f'<span class="stat"><b>{n_watch}</b><small>watch</small></span>'
        f'<span class="stat"><b>{n_noise}</b><small>noise hidden</small></span>', unsafe_allow_html=True)

    if len(eod_alerts) or len(fil_alerts):
        with st.container(border=True):
            st.markdown("**🔔 Triggered now**")
            for _, r in eod_alerts.head(6).iterrows():
                st.markdown(f"- **{r.symbol}** · " + " · ".join(r["flags"]))
            for _, r in fil_alerts.head(6).iterrows():
                st.markdown(f"- **{r.symbol}** · {r.category.replace('_', ' ')} · {html.escape(r.subject[:90])}")

    TIER_CLASS = {"Act now": "act", "Watch": "watch", "Noise": "noise"}

    def render_card(r, i, expanded=False):
        sym_name = config.META.get(r.symbol, {}).get("name", r.symbol)
        mv = moves.get(r.symbol)
        mv_html = f' · <span class="pct {"up" if mv >= 0 else "dn"}">{mv:+.1f}% today</span>' if mv is not None else ""
        st.markdown(
            f'<div class="card {TIER_CLASS[r.tier]}"><div class="top"><span class="sym">{r.symbol}</span>'
            f'{chip(r.category.replace("_", " "), "cat")}<span class="score">score {r.score}{mv_html}</span>'
            f'<span class="when">{r.time:%d %b, %H:%M}</span></div>'
            f'<div class="subj">{html.escape(r.subject)}</div></div>', unsafe_allow_html=True)
        with st.expander(f"Why it ranks here · details · {sym_name}", expanded=expanded):
            if ai_lines and r.tier == "Act now":
                try:
                    st.markdown("**In one line:** " + llm.complete(
                        "In <=25 words, plain English, summarise this exchange filing for an investor. Facts only, no advice.\n"
                        f"Subject: {r.subject}\nText: {r.detail[:1500]}", "You are a precise financial summariser.", "cheap", 120))
                except Exception as e:  # noqa: BLE001
                    st.caption(f"AI summary unavailable ({type(e).__name__}).")
            st.markdown("".join(chip(x) for x in r.reasons), unsafe_allow_html=True)
            if r.detail and r.detail.lower() != "nan":
                st.write(r.detail[:420] + ("…" if len(r.detail) > 420 else ""))
            if r.url and str(r.url).startswith("http"):
                st.markdown(f"[Open the filing ↗]({r.url})")

            if r.category == "results":
                if st.button("📊 First cut: Revenue · EBITDA · PBT · PAT (QoQ / YoY)", key=f"res{i}{r.symbol}"):
                    with st.spinner("Reading the filing…"):
                        show_headline(headline_numbers(r.symbol, r.url if isinstance(r.url, str) else ""))
            if r.category == "management_change":
                name, role = filings.person_from_filing(f"{r.subject}. {r.detail}")
                if name or role:
                    st.markdown(f"**Who:** {name or 'name not in headline'} · {role or 'role n/a'}")
                snip = filings.extract_profile_snippet(r.detail)
                if snip:
                    st.markdown("**Background (from the filing itself):**")
                    st.caption(snip)
                if name:
                    st.markdown(f"[Search {name} on LinkedIn ↗]({filings.linkedin_search_url(name, sym_name)})")
                if r.url and str(r.url).lower().split('?')[0].endswith(".pdf") and not snip:
                    if st.button("Read profile from the filing PDF", key=f"prof{i}{r.symbol}"):
                        try:
                            data, _ = research.download_doc(r.url)
                            txt = results.pdf_bytes_to_text(data, max_pages=4)
                            st.caption(filings.extract_profile_snippet(txt) or "No profile paragraph found in the filing.")
                        except Exception as e:  # noqa: BLE001
                            st.warning(f"Could not read the filing: {type(e).__name__}")
            if st.button("🔎 Dig deeper on " + r.symbol, key=f"dig{i}{r.symbol}"):
                ss["research_symbol"] = r.symbol
                st.info(f"{r.symbol} loaded in **Ask FINN** - open that tab.")

    if enriched.empty:
        st.info("Nothing in this window.")
    else:
        act = enriched[enriched["tier"] == "Act now"]
        watch = enriched[enriched["tier"] == "Watch"]
        noise = enriched[enriched["tier"] == "Noise"]
        if len(act):
            st.markdown("#### 🔴 Act now")
            for i, (_, r) in enumerate(act.iterrows()):
                render_card(r, f"a{i}", expanded=(i < 2))
        if len(watch):
            st.markdown("#### 🟡 Watch")
            for i, (_, r) in enumerate(watch.iterrows()):
                render_card(r, f"w{i}")
        if len(noise):
            with st.expander(f"🗂 {len(noise)} routine items collapsed (ESOP, compliance, notices…)"):
                st.dataframe(noise[["time", "symbol", "category", "subject"]], hide_index=True, use_container_width=True)

# ============================================================= RADAR (push)
with tab_radar:
    st.markdown(status_chips, unsafe_allow_html=True)
    if radar.empty:
        st.error("No end-of-day data available. Nothing is shown rather than anything invented.")
        if eod_errors:
            with st.expander("Errors"):
                st.json(eod_errors)
    else:
        as_of = radar["as_of"].max()
        st.caption(f"Session: **{as_of:%a %d %b %Y}** · rules: volume ≥ {ss['vol_mult']:.1f}× {config.DEFAULTS['lookback']}-day avg, "
                   f"price ≥ ±{ss['price_pct']:.1f}% · source: Yahoo Finance EOD")

        def spark(vals, color):
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1
            pts = " ".join(f"{i * 100 / (len(vals) - 1):.1f},{28 - (v - lo) * 26 / span:.1f}" for i, v in enumerate(vals))
            return (f'<svg viewBox="0 0 100 30" width="100%" height="30" preserveAspectRatio="none">'
                    f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{pts}"/></svg>')

        rows_html = ""
        for _, r in radar.iterrows():
            cls = "up" if r.pct >= 0 else "dn"
            color = "#14693e" if r.pct >= 0 else "#c0392b"
            flags = "".join(f'<span class="flag">{html.escape(f)}</span> ' for f in r["flags"])
            stale = chip("stale", "warn") if r.stale else ""
            rows_html += (
                f'<div class="rrow"><div class="l"><b>{r.symbol}</b><small>{html.escape(r["name"])} · {html.escape(r.sector)}</small></div>'
                f'<div class="m">{spark(r.spark, color)}</div>'
                f'<div class="r"><div>₹{r.close:,.1f} <span class="pct {cls}">{r.pct:+.2f}%</span></div>'
                f'<small>vol {r.vol_ratio:.1f}× avg {stale}</small><div>{flags}</div></div></div>')
        st.markdown(rows_html, unsafe_allow_html=True)
        with st.expander("Show the calculations (evidence first)"):
            show = radar[["symbol", "as_of", "close", "prev_close", "pct", "volume", "avg_volume", "vol_ratio", "ret_5d", "calc"]]
            st.dataframe(show, hide_index=True, use_container_width=True)
        if eod_errors:
            with st.expander(f"{len(eod_errors)} ticker(s) failed to load"):
                st.json(eod_errors)

# ============================================================ ASK FINN (pull)
with tab_ask:
    st.caption("Primary sources only: annual reports + earnings-call transcripts. No news, no broker notes, "
               "no model memory of the company. Works for any NSE ticker.")
    cols = st.columns(len(config.RESEARCH_DEMO))
    for col, t in zip(cols, config.RESEARCH_DEMO):
        if col.button(t, use_container_width=True, key=f"demo_{t}"):
            ss["research_symbol"] = t
    sym = st.text_input("NSE ticker", value=ss["research_symbol"]).upper().strip().replace(".NS", "")
    ss["research_symbol"] = sym

    mode = st.radio("Documents", ["Fetch from screener.in", "Upload PDFs"], horizontal=True)
    kb = ss["kb"].get(sym)

    if mode == "Fetch from screener.in":
        if st.button("1 · Find documents for " + sym):
            with st.spinner("Looking up screener.in…"):
                docs, status = research.screener_docs(sym)
            ss.setdefault("found", {})[sym] = (docs, status)
        docs, status = ss.get("found", {}).get(sym, ([], None))
        if status and status != "ok":
            st.warning(status)
        if docs:
            ars = [d for d in docs if d["kind"] == "annual_report"]
            calls = [d for d in docs if d["kind"] == "concall"]
            pick_ar = st.multiselect("Annual reports", [d["label"] for d in ars], [d["label"] for d in ars[:1]])
            pick_calls = st.multiselect("Concall transcripts", [d["label"] for d in calls], [d["label"] for d in calls[:4]])
            if st.button("2 · Build knowledge base", type="primary"):
                chosen = [d for d in docs if d["label"] in pick_ar + pick_calls]
                chunks, bar, report = [], st.progress(0.0), []
                for n, d in enumerate(chosen):
                    try:
                        pages = load_pages(d["url"])
                        ch = research.chunk_pages(pages, d["label"], d["kind"])
                        chunks += ch
                        report.append(f"✅ {d['label']} · {len(pages)} pages · {len(ch)} passages")
                    except Exception as e:  # noqa: BLE001
                        report.append(f"❌ {d['label']} · {type(e).__name__}: {e}")
                    bar.progress((n + 1) / len(chosen))
                ss["kb"][sym] = {"index": research.Index(chunks), "docs": report, "labels": [d["label"] for d in chosen if d["kind"] == "concall"]}
                kb = ss["kb"][sym]
    else:
        kind_choice = st.selectbox("Treat uploads as", ["Auto-detect", "Annual report", "Concall transcript"])
        ups = st.file_uploader("Annual reports / transcripts (PDF)", type="pdf", accept_multiple_files=True)
        if ups and st.button("Build knowledge base from uploads", type="primary"):
            chunks, report, labels = [], [], []
            for f in ups:
                try:
                    pages = research.extract_pages(f.read(), "application/pdf")
                    low = f.name.lower()
                    kind = ("concall" if any(w in low for w in ("transcript", "concall", "call")) else "annual_report") \
                        if kind_choice == "Auto-detect" else ("annual_report" if kind_choice == "Annual report" else "concall")
                    label = f.name.rsplit(".", 1)[0][:40]
                    ch = research.chunk_pages(pages, label, kind)
                    chunks += ch
                    if kind == "concall":
                        labels.append(label)
                    report.append(f"✅ {f.name} ({kind}) · {len(pages)} pages · {len(ch)} passages")
                except Exception as e:  # noqa: BLE001
                    report.append(f"❌ {f.name} · {type(e).__name__}: {e}")
            ss["kb"][sym] = {"index": research.Index(chunks), "docs": report, "labels": labels}
            kb = ss["kb"][sym]

    if kb:
        with st.expander(f"📚 Knowledge base · {len(kb['index'].chunks)} passages", expanded=False):
            for line in kb["docs"]:
                st.write(line)
        quarterly, qerr = load_quarterly(sym)
        if quarterly is not None and not quarterly.empty:
            with st.expander("Quarterly numbers used for cross-checks (₹ crore · Yahoo, standardised from filings)"):
                q = quarterly.copy(); q.index = [d.strftime("%b %Y") for d in q.index]
                st.dataframe(q.round(1), use_container_width=True)
        else:
            st.caption(f"Quarterly numbers unavailable ({qerr or 'none returned'}) - narrative checks use documents only.")

        st.markdown("**Pick an analysis**")
        menu = list(research.SECTIONS.items())
        bcols = st.columns(2)
        clicked = None
        for n, (key, s) in enumerate(menu):
            if bcols[n % 2].button(s["title"], key=f"sec_{key}", use_container_width=True, help=s["ask"]):
                clicked = ("section", key)
        if bcols[len(menu) % 2].button("Promised vs delivered", key="pvd", use_container_width=True,
                                       help="Guidance from the last 4 calls vs what the numbers delivered"):
            clicked = ("pvd", None)
        with st.form("ask", clear_on_submit=True):
            q_text = st.text_input("…or ask anything about this company", placeholder="e.g. What did management say about margin pressure?")
            if st.form_submit_button("Ask") and q_text.strip():
                clicked = ("ask", q_text.strip())

        if clicked:
            with st.spinner("Reading the documents…"):
                if clicked[0] == "section":
                    out = research.run_section(kb["index"], clicked[1], quarterly, sym)
                    title = research.SECTIONS[clicked[1]]["title"]
                elif clicked[0] == "pvd":
                    out = research.promised_vs_delivered(kb["index"], quarterly, sym, kb.get("labels", []))
                    title = "Promised vs delivered"
                    out.setdefault("hits", [])
                else:
                    out = research.answer_question(kb["index"], clicked[1], sym, quarterly)
                    title = "Q: " + clicked[1]
            ss["outputs"].insert(0, {"sym": sym, "title": title, "out": out})

        for o in [x for x in ss["outputs"] if x["sym"] == sym][:6]:
            out = o["out"]
            with st.container(border=True):
                st.markdown(f"**{o['title']}**")
                mode_txt = out.get("mode", "")
                kind = "live" if mode_txt.startswith("llm") else ("bad" if mode_txt in ("abstained", "none") else "warn")
                st.markdown(chip(f"mode: {mode_txt}", kind), unsafe_allow_html=True)
                if out.get("text"):
                    st.markdown(out["text"])
                elif out.get("guidance"):
                    st.caption("Extractive mode (no LLM key): guidance-bearing sentences found in the transcripts.")
                    st.dataframe(pd.DataFrame(out["guidance"]), hide_index=True, use_container_width=True)
                elif out.get("hits"):
                    st.caption("Extractive mode (no LLM key): the most relevant passages, with page references.")
                    for score_, c in out["hits"][:3]:
                        st.markdown(f"> {c['text'][:480]}…  \n> — *{research.cite(c)}*")
                else:
                    st.markdown("**Not found in the provided documents.**")
                if out.get("hits"):
                    with st.expander(f"Evidence · {len(out['hits'])} passages"):
                        for score_, c in out["hits"]:
                            st.markdown(f"**{research.cite(c)}** · relevance {score_:.2f}")
                            st.caption(c["text"][:600])
    else:
        st.info("Build a knowledge base to unlock the analyses.")

# ---------------------------------------------------------------------- footer
with st.expander("ℹ️ How FINN works · cost · what's deliberately left out"):
    s = llm.STATS
    st.markdown(f"""
**Triage, not dashboard.** Filings are classified by transparent rules (17 ENAM categories) and scored additively - every point is shown under *Why it ranks here*. A stock move that confirms a filing adds weight.

**Evidence first.** Every research claim carries `[Doc p.N]`. If retrieval finds nothing relevant, FINN says *Not found in the provided documents* instead of guessing.

**Cost discipline.** Rules for triage (free) · TF-IDF retrieval (free, local) · cheap model for extraction/summaries · strong model only for *narrative vs numbers*, *bull vs bear* and *promised vs delivered* · identical prompts cached on disk.
Session usage → calls: **{s['calls']}**, cache hits: **{s['cache_hits']}**, tokens in/out: **{s['tokens_in']:,} / {s['tokens_out']:,}**.

**Left out on purpose:** automated NSE/BSE monitoring at scale (endpoints are fragile), LinkedIn scraping (ToS) - a search link is provided instead, intraday data, portfolio P&L.
""")
