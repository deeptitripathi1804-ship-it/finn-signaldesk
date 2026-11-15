"""Offline tests for the pure logic (no network, no Streamlit, no yfinance needed).
Run:  python -m unittest discover -s tests -v
"""
import datetime as dt
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import filings, market, research, results  # noqa: E402


def _frame(n=40, last_vol_mult=1.0, last_move=0.0):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    close = np.full(n, 100.0)
    close[-1] = 100.0 * (1 + last_move / 100)
    vol = np.full(n, 1_000_000.0)
    vol[-1] = 1_000_000.0 * last_vol_mult
    return pd.DataFrame({"Close": close, "Volume": vol}, index=idx)


class TestMarket(unittest.TestCase):
    def test_volume_spike_flagged(self):
        s = market.compute_signal(_frame(last_vol_mult=2.7), vol_mult=2.0, price_pct=3.0)
        self.assertAlmostEqual(s["vol_ratio"], 2.7, places=3)
        self.assertTrue(s["flagged"])
        self.assertTrue(any("Volume" in f for f in s["flags"]))

    def test_price_move_flagged_both_directions(self):
        for mv in (4.5, -4.5):
            s = market.compute_signal(_frame(last_move=mv), 2.0, 3.0)
            self.assertAlmostEqual(s["pct"], mv, places=3)
            self.assertTrue(any("Price" in f for f in s["flags"]))

    def test_quiet_day_not_flagged(self):
        self.assertFalse(market.compute_signal(_frame(last_vol_mult=1.1, last_move=0.5), 2.0, 3.0)["flagged"])

    def test_threshold_is_user_configurable(self):
        f = _frame(last_vol_mult=1.5)
        self.assertFalse(market.compute_signal(f, 2.0, 3.0)["flagged"])
        self.assertTrue(market.compute_signal(f, 1.3, 3.0)["flagged"])

    def test_baseline_excludes_today(self):
        # if today's spike leaked into the average, the ratio would be < 2.0
        s = market.compute_signal(_frame(last_vol_mult=2.0), 1.0, 99.0)
        self.assertAlmostEqual(s["vol_ratio"], 2.0, places=6)

    def test_too_little_history_returns_none(self):
        self.assertIsNone(market.compute_signal(_frame(n=10), 2.0, 3.0))


class TestClassifier(unittest.TestCase):
    CASES = [
        ("Outcome of Board Meeting - Unaudited Financial Results for the quarter ended September 30", "results"),
        ("Appointment of Chief Financial Officer", "management_change"),
        ("Resignation of Chief Executive Officer", "management_change"),
        ("Credit rating reaffirmed by CRISIL", "credit_rating"),
        ("Acquisition of 100% stake in a US-based firm", "M&A"),
        ("Intimation of Record Date for payment of dividend", "dividend"),
        ("Raising of funds through Non-Convertible Debentures", "fund_raise"),
        ("Receipt of show cause notice from regulatory authority", "litigation"),
        ("Disclosure under Regulation 7(2) - acquisition by promoter", "insider_trading"),
        ("Transcript of earnings call", "earnings_call"),
        ("Notice of Postal Ballot", "postal_ballot"),
        ("Allotment of equity shares under ESOP", "unclassified"),
        ("Appointment of Secretarial Auditor", "unclassified"),
        ("Trading Window Closure", "unclassified"),
        ("Approval of capex for new plant", "capex"),
        ("Bonus issue and stock split", "bonus_split"),
        ("Pledge invoked by lender", "pledging"),
    ]

    def test_categories(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(filings.classify(text)[0], want)

    def test_priority_ordering(self):
        def sc(text, **kw):
            c, _ = filings.classify(text)
            return filings.score(c, text, **kw)[0]
        self.assertGreater(sc("Unaudited Financial Results for the quarter"), sc("Allotment under ESOP"))
        self.assertGreater(sc("Resignation of CFO"), sc("Appointment of Independent Director"))
        self.assertGreater(sc("Credit rating downgrade by ICRA"), sc("Credit rating reaffirmed by ICRA"))

    def test_price_confirmation_boost_and_explanations(self):
        base, _ = filings.score("press_release", "order received", move_pct=0.5)
        boosted, why = filings.score("press_release", "order received", move_pct=5.0)
        self.assertEqual(boosted - base, 8)
        self.assertTrue(any("moved" in w for w in why))

    def test_intimation_is_demoted(self):
        a, _ = filings.score("results", "Intimation of board meeting to consider financial results")
        b, _ = filings.score("results", "Outcome: financial results")
        self.assertLess(a, b)

    def test_enrich_tiers_and_noise_collapse(self):
        df = filings.demo_filings({"TITAN", "DIXON", "ASTRAL", "DMART"})
        e = filings.enrich(df)
        self.assertEqual(e.iloc[0]["tier"], "Act now")
        self.assertTrue((e[e["symbol"].isin(["ASTRAL", "DMART"])]["tier"] == "Noise").all())
        self.assertTrue((e["subject"].str.startswith("[DEMO]")).all())  # demo rows can never pass as real

    def test_person_extraction(self):
        n, r = filings.person_from_filing("Appointment of Mr. A. Sample as Chief Financial Officer")
        self.assertIn("Sample", n)
        self.assertIn("chief financial", r.lower())
        self.assertIn("20+ years", filings.extract_profile_snippet("Brief profile: 20+ years in finance. Prior role X."))

    def test_csv_import_flexible_headers(self):
        csv = "SYMBOL,BROADCAST DATE/TIME,SUBJECT,DETAILS,ATTACHMENT\nTITAN,13-Nov-2026 18:30:00,Financial Results,x,http://a/b.pdf\n"
        df = filings.load_csv(csv)
        self.assertEqual(df.iloc[0]["symbol"], "TITAN")
        self.assertEqual(df.iloc[0]["time"].day, 13)
        with self.assertRaises(ValueError):
            filings.load_csv("a,b\n1,2\n")


RESULTS_TEXT = """
Statement of Unaudited Standalone Financial Results (Rs. in crore)
Particulars Quarter ended 30.09.2026 Quarter ended 30.06.2026 Quarter ended 30.09.2025
1 Revenue from operations 14,500.00 13,000.00 12,000.00
2 Other income 100.00 90.00 80.00
4 Finance costs 400.00 380.00 350.00
5 Depreciation and amortisation expense 600.00 590.00 550.00
Profit before exceptional items and tax 1,900.00 1,700.00 1,500.00
Profit before tax 1,900.00 1,700.00 1,500.00
Net profit for the period 1,400.00 (1,200.00) 1,100.00
"""


class TestResults(unittest.TestCase):
    def test_filing_text_parse_and_pct(self):
        p = results.parse_results_text(RESULTS_TEXT)
        self.assertEqual(p["unit"], "₹ crore")
        self.assertEqual(p["rows"]["revenue"], [14500.0, 13000.0, 12000.0])
        self.assertEqual(p["rows"]["pbt"], [1900.0, 1700.0, 1500.0])      # skipped the 'before exceptional' row
        self.assertEqual(p["rows"]["pat"][1], -1200.0)                    # parentheses -> negative
        self.assertEqual(p["rows"]["ebitda"], [2900.0, 2670.0, 2400.0])   # PBT + finance + dep
        t = results.table_from_parsed(p).set_index("Metric")
        self.assertAlmostEqual(t.at["Revenue", "QoQ %"], (14500 - 13000) / 13000 * 100, places=6)
        self.assertAlmostEqual(t.at["Revenue", "YoY %"], (14500 - 12000) / 12000 * 100, places=6)
        # negative base uses abs() so growth from a loss is not reported with a flipped sign
        self.assertGreater(t.at["PAT", "QoQ %"], 0)

    def test_unparseable_filing_is_reported_not_faked(self):
        p = results.parse_results_text("Nothing tabular here at all")
        self.assertEqual(p["rows"], {})
        self.assertTrue(any("not found" in n for n in p["notes"]))

    def test_yahoo_quarterly_yoy_matched_by_date(self):
        cols = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
        stmt = pd.DataFrame({c: [v * 1e7 for v in vals] for c, vals in zip(
            cols, [[1500, 300, 250, 200], [1400, 280, 240, 190], [1300, 260, 220, 170],
                   [1200, 240, 200, 160], [1000, 200, 180, 150]])},
            index=["Total Revenue", "EBITDA", "Pretax Income", "Net Income"])
        ch = results.changes_from_quarterly(results.quarterly_from_stmt(stmt))
        t = ch["table"].set_index("Metric")
        self.assertEqual(ch["yoy"], pd.Timestamp("2025-09-30"))
        self.assertAlmostEqual(t.at["Revenue", "YoY %"], 50.0, places=6)
        self.assertAlmostEqual(t.at["Revenue", "QoQ %"], (1500 - 1400) / 1400 * 100, places=6)

    def test_missing_year_ago_quarter_gives_none_not_wrong_number(self):
        cols = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31"])
        stmt = pd.DataFrame({c: [1e10] for c in cols}, index=["Total Revenue"])
        ch = results.changes_from_quarterly(results.quarterly_from_stmt(stmt))
        self.assertIsNone(ch["yoy"])
        self.assertIsNone(ch["table"].set_index("Metric").at["Revenue", "YoY %"])


def _kb():
    pages = {
        ("AR FY26", "annual_report"): [
            (4, "The company operates three segments: jewellery, watches and wearables, and eyecare. "
                "Jewellery contributes the majority of revenue, around eighty percent of total sales in the year."),
            (61, "Borrowings stood at Rs 3,000 crore. Net debt to equity improved. Working capital days: inventory 140 days, "
                 "receivables 12 days, payables 20 days."),
        ],
        ("Concall Sep 2026", "concall"): [
            (3, "We expect jewellery revenue growth of 20% for FY27 and we aim to keep EBITDA margin around 11 to 12 percent. "
                "Capex guidance for the year is Rs 400 crore towards new stores and a plant."),
        ],
    }
    chunks = []
    for (label, kind), pg in pages.items():
        chunks += research.chunk_pages(pg, label, kind)
    return research.Index(chunks)


class TestResearch(unittest.TestCase):
    def test_retrieval_returns_right_page_with_citation(self):
        idx = _kb()
        hits = idx.search("inventory days working capital", k=3)
        self.assertEqual(hits[0][1]["page"], 61)
        self.assertEqual(research.cite(hits[0][1]), "[AR FY26 p.61]")

    def test_kind_filter(self):
        hits = _kb().search("guidance expect capex", k=5, kinds=["concall"])
        self.assertTrue(hits and all(c["kind"] == "concall" for _, c in hits))

    def test_run_section_does_not_bypass_kind_filter(self):
        class EmptyIndex:
            def __init__(self):
                self.calls = []

            def search(self, *args, **kwargs):
                self.calls.append(kwargs)
                return []

        idx = EmptyIndex()
        out = research.run_section(idx, "business")

        self.assertEqual(len(idx.calls), 1)
        self.assertEqual(idx.calls[0]["kinds"], ["annual_report"])
        self.assertEqual(out["mode"], "none")

    def test_abstains_on_unanswerable_question(self):
        out = research.answer_question(
            _kb(),
            "What is the CEO's favourite cricket team and pet's name?",
            "TITAN"
        )
        self.assertEqual(out["mode"], "abstained")
        self.assertIn("Not found", out["text"])

    def test_extractive_guidance_finds_numbers(self):
        g = research.extractive_guidance(_kb())
        self.assertTrue(any("20%" in x["sentence"] for x in g))
        self.assertTrue(all(x["doc"].startswith("Concall") for x in g))

    def test_chunks_carry_page_numbers(self):
        for c in _kb().chunks:
            self.assertIn("page", c)
            self.assertIn("doc", c)

    def test_valid_citation_is_accepted(self):
        hits = _kb().search("inventory days working capital", k=3)
        text = "Inventory stood at 140 days [AR FY26 p.61]."
        self.assertTrue(research.citations_valid(text, hits))

    def test_fabricated_page_citation_is_rejected(self):
        hits = _kb().search("inventory days working capital", k=3)
        text = "Inventory stood at 140 days [AR FY26 p.999]."
        self.assertFalse(research.citations_valid(text, hits))

    def test_missing_citation_is_rejected(self):
        hits = _kb().search("inventory days working capital", k=3)
        text = "Inventory stood at 140 days."
        self.assertFalse(research.citations_valid(text, hits))

    def test_mixed_valid_and_invalid_citations_are_rejected(self):
        hits = _kb().search("inventory days working capital", k=3)
        text = "Inventory stood at 140 days [AR FY26 p.61] [AR FY26 p.999]."
        self.assertFalse(research.citations_valid(text, hits))

if __name__ == "__main__":
    unittest.main(verbosity=2)
