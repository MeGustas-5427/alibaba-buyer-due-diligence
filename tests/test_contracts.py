import copy
from pathlib import Path
import sys
import tempfile
import unittest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"examples")]
from extract_research_view import (Invalid, HEADERS, atomic, digest, now, project, public_url, safe_text, validate_pair)
from synthetic_demo import source, workbook, prepare, fill
from validate_report import validate_research
import workflow as w


class ContractsTests(unittest.TestCase):
    def test_projection_dictionary_missing_homepage_and_internal_year(self):
        payload = source()
        view = project(payload)
        self.assertEqual(len(view["records"][0]), 26)
        self.assertEqual(view["records"][0]["businessType"], "批发/分销商")
        self.assertEqual(view["records"][0]["activityLoginDays90d"], 0)
        self.assertEqual(view["records"][0]["registerYear"], 2020)
        payload["records"][0].update(buyerHomepageStatus="not_collected_no_profile_link", buyerHomepage=None)
        self.assertEqual(len(project(payload)["records"]), 3)
        self.assertIsNone(project(payload)["records"][0]["registerYear"])

    def test_identity_date_status_rejects(self):
        mutations = [lambda p: p.update(sourceDate="2026-02-30"),
            lambda p: p["records"].append(copy.deepcopy(p["records"][0])),
            lambda p: p["records"][0]["buyerHomepage"]["buyerProfile"].update(memberId="wrong"),
            lambda p: p["records"][0].update(buyerHomepageStatus="not_collected_no_profile_link")]
        for change in mutations:
            with self.subTest(change=change):
                payload = source()
                change(payload)
                with self.assertRaises(ValueError):
                    project(payload)

    def test_xlsx_exact_all_sheets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = source(11)
            atomic(root/"input.json", payload)
            workbook(root/"input.xlsx", payload)
            self.assertEqual(len(validate_pair(root/"input.json", root/"input.xlsx")["records"]), 11)
            for sheet, cell, replacement in [("背调输入", "E2", "changed"), ("批次02", "A2", 1), ("生成说明", "B4", "2026-10-03"), ("背调输入", "P2", "=0")]:
                workbook(root/"input.xlsx", payload)
                book = load_workbook(root/"input.xlsx")
                book[sheet][cell] = replacement
                book.save(root/"input.xlsx")
                book.close()
                with self.assertRaises(Invalid):
                    validate_pair(root/"input.json", root/"input.xlsx")

    def test_privacy_urls_and_embedded_private_link(self):
        for url in ["https://example.com?token=synthetic", "https://user:pass@example.com", "http://127.0.0.1/x", "https://profile.alibaba.com/profile/my_profile.htm?foo=bar", "https://example.com/#session=synthetic"]:
            with self.subTest(url=url), self.assertRaises(Invalid):
                public_url(url)
        for text in ["See https://example.com/?api_key=synthetic please", "mail synthetic@example.com", "+86 123 456 78901", "Cookie: synthetic"]:
            with self.subTest(text=text), self.assertRaises(Invalid):
                safe_text(text)
        self.assertEqual(public_url("https://www.example.com/a?utm_source=x#anchor"), "https://www.example.com/a")

    def test_research_bad_scores_references_sources_and_screening(self):
        with tempfile.TemporaryDirectory() as temp:
            run = prepare(temp)
            data = fill(run, 2)
            state = w.load_state(run)
            view = w.inputs(state)
            def check(d):
                return validate_research(d, view, state["inputs"]["json"]["sha256"], run)
            self.assertEqual(check(data)["dimensionDenominator"], 27)
            mutations = [lambda r: r.pop("matchStatus"), lambda r: r.update(identificationStrength="low"),
                lambda r: r["scores"]["risk"].update(value=float("nan")), lambda r: r["scores"]["risk"].update(value=True),
                lambda r: r["scores"]["risk"].update(sourceIndexes=[99]),
                lambda r: r["sources"][1].update(url=r["sources"][0]["url"]+"?utm=1"),
                lambda r: r["sources"][1].update(publisherGroup=r["sources"][0]["publisherGroup"]),
                lambda r: r.update(publicSummary="Private identity synthetic-login-00 must not appear"),
                lambda r: r["screening"].update(status="passed")]
            for mutation in mutations:
                modified = copy.deepcopy(data)
                mutation(modified["records"][0])
                with self.subTest(mutation=mutation), self.assertRaises((Invalid, ValueError)):
                    check(modified)
            data["records"][2]["scores"] = {"overall": 0}
            with self.assertRaises(Invalid):
                check(data)

    def test_missing_complete_list_receipt_and_terms_change_rejected(self):
        from screen_uk_sanctions import URL, MEMORY_BYTES, WALL_SECONDS
        with tempfile.TemporaryDirectory() as temp:
            run = prepare(temp, 1)
            data = fill(run, 1)
            state = w.load_state(run)
            view = w.inputs(state)
            atomic(run/"terms.json", [view["records"][0]["companyName"]])
            receipt = {"schemaVersion": "alibaba.uk-screening.v1", "complete": True, "eofReached": True,
                "termsSha256": digest(run/"terms.json"), "termCount": 1, "rows": 1200,
                "matchedMethod": "NFKC-casefold-whitespace-exact-name", "hits": [], "hitCount": 0, "checkedAt": now(),
                "source": {"url": URL, "sha256": "a"*64, "bytes": 12000},
                "guard": {"limitReadbackVerified": True, "workerExitCode": 0, "memoryLimitBytes": MEMORY_BYTES, "wallLimitSeconds": WALL_SECONDS}}
            # This receipt is wholly simulated for a negative-control test, never a real screening claim.
            atomic(run/"uk-screening.json", receipt)
            data["records"][0]["screening"].update(status="passed", termsFile="terms.json", receipt="uk-screening.json", reason="SYNTHETIC exact-only test; no real compliance clearance")
            def check():
                validate_research(data, view, state["inputs"]["json"]["sha256"], run)
            check()
            (run/"uk-screening.json").unlink()
            with self.assertRaises(Invalid):
                check()
            atomic(run/"uk-screening.json", receipt)
            atomic(run/"terms.json", [view["records"][0]["companyName"], "SYNTHETIC extra name"])
            with self.assertRaises(Invalid):
                check()


if __name__ == "__main__":
    unittest.main()
