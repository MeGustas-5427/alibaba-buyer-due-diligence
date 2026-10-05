"""Native route decisions and synthetic helper process contract; no browser/AHK access."""
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"examples")]
import workflow as w
from collection import collect as c, browser_helper as bh
from extract_research_view import Invalid, atomic, digest, now, read_json


class NativeBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Match the production entrypoint: Windows TEMP may contain an 8.3 alias.
        self.base = Path(self.temp.name).resolve()
        self.run = Path(w.initialize(None, None, self.base, collect_config=c.configuration("2026-10-04"))["run"])
        self.allowed = {"devtools_authorized": True, "host_allows_devtools": True}

    def tearDown(self):
        self.temp.cleanup()

    def test_current_user_approval_and_host_permission_are_both_required(self):
        for user, host, expected in [(False, False, "NATIVE_DEVTOOLS_REQUIRED"),
                                     (True, False, "HOST_CHANNEL_NOT_ALLOWED"),
                                     (False, True, "REVIEW_BROWSER_AUTHORIZATION"),
                                     (True, True, "CHECK_AUTHORIZED_DEVTOOLS")]:
            with self.subTest(user=user, host=host):
                result = c.diagnose(self.run, devtools_authorized=user, host_allows_devtools=host)
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["requiredBrowserChannel"], "native_chrome_devtools")
                self.assertFalse(result["collectionAllowed"])
                self.assertEqual(result["status"], "check" if user and host else "stop")
                self.assertFalse(result["automaticRetry"])

    def test_site_stops_win_in_any_signal_order_even_with_all_approvals(self):
        for site in c.SITE_STOPS:
            for signals in itertools.permutations(("devtools_failed", "cim_query_failed", site)):
                with self.subTest(signals=signals):
                    result = c.diagnose(self.run, signals, **self.allowed, ahk_authorized=True)
                    self.assertEqual(result["code"], "SITE_GATE_BLOCKED")
                    self.assertEqual(result["status"], "stop")
                    self.assertFalse(result["collectionAllowed"])

    def test_cim_failure_and_unknown_helper_are_not_connection_or_absence_proofs(self):
        result = c.diagnose(self.run, ["cim_query_failed"], **self.allowed)
        self.assertEqual(result["code"], "CHECK_AHK_WITHOUT_CIM")
        self.assertEqual(result["diagnosticScope"], "read_only_process_inspection")
        self.assertFalse(result["authorizationObserved"]["ahkHelper"])
        self.assertEqual(result["siteState"], "unknown")
        for signal, expected in [("ahk_identity_unverified", "AHK_IDENTITY_UNVERIFIED"),
                                 ("devtools_failed", "DEVTOOLS_CONNECTION_FAILED"),
                                 ("receiver_identity_mismatch", "RECEIVER_IDENTITY_MISMATCH")]:
            self.assertEqual(c.diagnose(self.run, [signal], **self.allowed)["code"], expected)

    def test_saved_failures_and_lifecycle_cannot_be_cleared_by_transport_flags(self):
        state = w.load_state(self.run)
        for status in ("paused", "blocked", "cancelled"):
            state["status"] = status
            w.save_state(self.run, state)
            self.assertEqual(c.diagnose(self.run, **self.allowed)["code"], "RUN_NOT_ACTIVE")
        state["status"] = "active"
        state["lastError"] = "Synthetic failed validator"
        w.save_state(self.run, state)
        self.assertEqual(c.diagnose(self.run, **self.allowed)["code"], "VALIDATOR_FAILED")
        state["lastError"] = None
        w.save_state(self.run, state)
        atomic(c.folder(self.run)/"collector_errors.jsonl", json.dumps({"kind": "list", "error": "SYNTHETIC failure"})+"\n")
        self.assertEqual(c.diagnose(self.run, **self.allowed)["code"], "UNRESOLVED_COLLECTION_ERRORS")

    def test_rule_changes_and_diagnostics_read_only_and_exit_codes(self):
        before = {str(p.relative_to(self.run)): digest(p) for p in self.run.rglob("*") if p.is_file()}
        with patch.object(w, "rules_hash", return_value="different"):
            self.assertEqual(c.diagnose(self.run, **self.allowed)["code"], "RULES_CHANGED")
        command = [sys.executable, "-X", "utf8", str(ROOT/"scripts/workflow.py"), "diagnose", "--run", str(self.run),
                   "--devtools-authorized", "--host-allows-devtools"]
        for extra, exit_code, code in [([], 0, "CHECK_AUTHORIZED_DEVTOOLS"), (["--signal", "captcha"], 2, "SITE_GATE_BLOCKED")]:
            result = subprocess.run(command+extra, capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, exit_code, result.stderr)
            self.assertEqual(json.loads(result.stdout)["code"], code)
        after = {str(p.relative_to(self.run)): digest(p) for p in self.run.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_removed_extension_signal_is_rejected_without_changing_run(self):
        before = digest(self.run/"workflow_state.json")
        with self.assertRaises(Invalid):
            c.diagnose(self.run, ["extension_failed"], **self.allowed)
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT/"scripts/workflow.py"), "diagnose",
                                 "--run", str(self.run), "--signal", "extension_failed"],
                                capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, digest(self.run/"workflow_state.json"))

    @unittest.skipUnless(os.name == "nt", "Windows helper guard")
    def test_helper_launch_requires_separate_approval_and_no_blockers(self):
        with patch.object(bh.subprocess, "run") as start:
            for options in ({}, self.allowed, {**self.allowed, "ahk_authorized": True}):
                with self.subTest(options=options), self.assertRaises(Invalid):
                    bh.operate(self.run, "start", **options)
            with self.assertRaises(Invalid):
                bh.operate(self.run, "start", **self.allowed, ahk_authorized=True,
                           approval="SYNTHETIC explicit helper approval", host_policy="SYNTHETIC allowed host",
                           signals=["captcha"])
            start.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows helper execution policy")
    def test_helper_policy_rejection_does_not_retry_or_bypass(self):
        executable = self.base/"AutoHotkey64.exe"
        atomic(executable, "SYNTHETIC never executed")
        denied = subprocess.CompletedProcess([], 1, "", "SYNTHETIC UnauthorizedAccess: execution policy")
        before = {str(p.relative_to(self.run)): digest(p) for p in self.run.rglob("*") if p.is_file()}
        with patch.object(bh.subprocess, "run", return_value=denied) as command:
            with self.assertRaises(Invalid):
                bh.operate(self.run, "inspect", executable=executable)
            command.assert_called_once()
            args = command.call_args.args[0]
            self.assertIn("-File", args)
            self.assertNotIn("-ExecutionPolicy", args)
        self.assertEqual(before, {str(p.relative_to(self.run)): digest(p) for p in self.run.rglob("*") if p.is_file()})

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell process contract")
    def test_powershell_helper_lifecycle_with_synthetic_process_provider(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        self.assertTrue(shell)
        fake_dir = self.base/"SYNTHETIC 辅助程序 space"
        fake_dir.mkdir()
        executable, script = fake_dir/"AutoHotkey64.exe", fake_dir/"synthetic helper.ahk"
        atomic(executable, "SYNTHETIC non-executable fixture")
        atomic(script, "; SYNTHETIC never executed")
        cases = [("absent", "inspect", 0, "absent"), ("unowned", "inspect", 0, "unverified"),
                 ("query_failed", "inspect", 2, None), ("verified", "inspect", 0, "verified"),
                 ("verified", "stop", 0, "stopped"), ("pid_reused", "stop", 2, None),
                 ("script_changed", "stop", 2, None), ("exited", "stop", 0, "exited"),
                 ("existing_start", "start", 2, None), ("stale_authority", "start", 2, None),
                 ("wrong_run_authority", "start", 2, None), ("missing_ahk_approval", "start", 2, None),
                 ("wrong_run_identity", "stop", 2, None), ("start", "start", 0, "started")]
        # Exercise the same runtime selection as the product, without relaxing OS execution policy.
        for case, action, expected_exit, expected_status in cases:
            with self.subTest(shell=Path(shell).name, case=case, action=action):
                case_run = Path(w.initialize(None, None, self.base, collect_config=c.configuration("2026-10-04"))["run"])
                base = c.folder(case_run)
                identity = {"runId": case_run.name, "run": str(case_run), "pid": 987654,
                            "executable": str(executable), "script": str(script),
                            "executableSha256": digest(executable), "scriptSha256": digest(script),
                            "startTimeUtcTicks": 639267552000000000}
                if case == "pid_reused":
                    identity["startTimeUtcTicks"] -= 1
                if case == "script_changed":
                    identity["scriptSha256"] = "0"*64
                if case == "wrong_run_identity":
                    identity["runId"] = "another-run"
                if case in {"verified", "pid_reused", "script_changed", "exited", "wrong_run_identity"}:
                    atomic(base/"browser-helper-identity.private.json", identity)
                authority = {
                    "schemaVersion": "alibaba.browser-helper-authorization.v1", "runId": case_run.name, "run": str(case_run),
                    "checkedAt": now(), "userApproval": "SYNTHETIC explicit approval", "hostPolicy": "SYNTHETIC allowed host",
                    "nativeDevtools": True, "hostAllowsDevtools": True, "ahkHelper": True,
                    "executable": str(executable), "script": str(script),
                    "executableSha256": digest(executable), "scriptSha256": digest(script)}
                if case == "stale_authority":
                    authority["checkedAt"] = "2020-01-01T00:00:00+00:00"
                if case == "wrong_run_authority":
                    authority["runId"] = "another-run"
                if case == "missing_ahk_approval":
                    authority["ahkHelper"] = False
                atomic(base/"browser-helper-authorization.private.json", authority)
                result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT/"tests/helper_harness.ps1"),
                                         "-Case", case, "-Action", action, "-RunPath", str(case_run), "-Executable", str(executable),
                                         "-ScriptFile", str(script), "-HelperScript", str(c.ROOT/"browser_helper.ps1")],
                                        capture_output=True, encoding="utf-8-sig", timeout=20)
                self.assertEqual(result.returncode, expected_exit, result.stdout+result.stderr)
                if expected_status:
                    self.assertEqual(json.loads(result.stdout)["status"], expected_status)
                self.assertEqual((case_run/"synthetic-kill-called").exists(), case == "verified" and action == "stop")
                self.assertEqual((case_run/"synthetic-start-called").exists(), case == "start")
                if case == "start":
                    saved = read_json(base/"browser-helper-identity.private.json")
                    self.assertEqual(saved["script"], str(script))
                    self.assertEqual(saved["pid"], 987654)


if __name__ == "__main__":
    unittest.main()
