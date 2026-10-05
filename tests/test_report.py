import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"examples")]
from extract_research_view import Invalid, atomic, digest, now, read_json
from synthetic_demo import prepare, fill
from generate_initial_screening_pdf import DEFAULT_TEMPLATE, build_html, filtered_customers, score_parts, find_browser
from validate_report import matched_content
import workflow as w


class ReportTests(unittest.TestCase):
    def test_null_not_zero_and_strict_filter(self):
        self.assertEqual(score_parts(None)[0], "未评估")
        self.assertEqual(score_parts(0)[0], "0.0")
        for value in (True, "", "8", -1, 11, float("nan")):
            with self.assertRaises(ValueError):
                score_parts(value)
        for row in ({}, {"match": "matched", "identificationStrength": "low", "公开来源": ["https://example.com"]}):
            with self.assertRaises(ValueError):
                filtered_customers([row])

    def test_seven_eight_boundary_and_long_name(self):
        with tempfile.TemporaryDirectory() as temp:
            run = prepare(temp, 9)
            data = fill(run, 8)
            view = w.inputs(w.load_state(run))
            content = matched_content(data, view)
            rendered = build_html(DEFAULT_TEMPLATE, content)
            self.assertEqual(rendered.count('class="page page-2"'), 8)
            self.assertIn("首页列示前 7 位，共 8 位", rendered)
            self.assertIn("未评估", rendered)
            self.assertIn("International Distribution", rendered)
            data["records"][7]["matchStatus"] = "unconfirmed"
            self.assertEqual(len(matched_content(data, view)["客户"]), 7)

    def test_real_browser_pdf_gate_and_tamper(self):
        try:
            find_browser()
        except FileNotFoundError:
            self.skipTest("Chrome/Edge unavailable; do not claim PDF validation")
        with tempfile.TemporaryDirectory() as temp:
            run = prepare(temp, 3)
            fill(run, 2)
            for _ in range(3):
                w.advance(run)
            with self.assertRaises(Invalid):
                w.advance(run)
            self.assertTrue((run/"matched.pdf").exists())
            self.assertEqual(w.status(run)["nextStage"], "reports_validated")
            atomic(run/"visual-review.json", {"pdfSha256": digest(run/"matched.pdf"), "status": "passed", "pagesReviewed": [1, 2, 3], "issues": [], "reviewer": "UNIT TEST SIMULATION", "checkedAt": now(), "notes": "Synthetic simulated gate only; no human visual claim."})
            w.advance(run)
            self.assertTrue(w.advance(run, finalize=True)["complete"])
            with (run/"matched.pdf").open("ab") as stream:
                stream.write(b"\nSYNTHETIC tamper")
            self.assertFalse(w.status(run)["complete"])
            self.assertEqual(w.status(run)["nextStage"], "reports_validated")


if __name__ == "__main__":
    unittest.main()
