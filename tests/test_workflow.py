"""Observable workflow and hook gates, with wholly invented data only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"examples"), str(ROOT/"hooks")]
from extract_research_view import Invalid, atomic, digest, now, read_json
from synthetic_demo import prepare, fill
import workflow as w
import codex_guard as h


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.run = prepare(self.base, 3, "synthetic-session")

    def tearDown(self):
        self.temp.cleanup()

    def advance_to_reports(self, matched=0):
        fill(self.run, matched)
        for _ in range(3):
            w.advance(self.run)

    def test_zero_match_full_run_and_cleanup(self):
        original = digest(self.base/"synthetic-input.json")
        self.advance_to_reports()
        w.advance(self.run)
        result = w.advance(self.run, finalize=True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "completed_with_limitations")
        self.assertFalse((self.run/"matched.pdf").exists())
        self.assertFalse((self.run/"research-view.tmp.json").exists())
        self.assertEqual(digest(self.base/"synthetic-input.json"), original)
        self.assertEqual(read_json(self.run/"validation.json")["customerCount"], 3)

    def test_no_shortcut_and_fake_flags(self):
        with self.assertRaises(Invalid):
            w.advance(self.run, finalize=True)
        state = w.load_state(self.run)
        state["checks"] = {s: "passed" for s in w.STAGES}
        state["status"] = "completed"
        w.save_state(self.run, state)
        self.assertFalse(w.status(self.run)["complete"])
        with self.assertRaises(Invalid):
            w.advance(self.run, finalize=True)

    def test_missing_customer_and_observation_rejected(self):
        data = fill(self.run, 0)
        data["records"].pop()
        atomic(self.run/"research.json", data)
        with self.assertRaises(Invalid):
            w.advance(self.run)
        fill(self.run, 0)
        (self.run/"evidence"/"synthetic-00.md").unlink()
        with self.assertRaises(Invalid):
            w.advance(self.run)

    def test_resume_mutation_invalidates_completion(self):
        self.advance_to_reports()
        w.advance(self.run)
        w.advance(self.run, finalize=True)
        atomic(self.run/"evidence"/"synthetic-00.md", "Modified SYNTHETIC observation")
        self.assertEqual(w.status(self.run)["nextStage"], "research_recorded")
        with self.assertRaises(Invalid):
            w.advance(self.run, finalize=True)
        self.assertEqual(read_json(self.run/"validation.json")["status"], "failed_validation")

    def test_input_changed_rejects(self):
        data = read_json(self.base/"synthetic-input.json")
        data["sourceDate"] = "2026-10-03"
        atomic(self.base/"synthetic-input.json", data)
        self.assertEqual(w.status(self.run)["nextStage"], "input_validated")
        with self.assertRaises(Invalid):
            w.advance(self.run)

    def test_concurrent_lock_and_atomic_state(self):
        with w.locked(self.run/".lock"):
            with ThreadPoolExecutor() as pool:
                with self.assertRaises(Invalid):
                    pool.submit(w.advance, self.run).result()
        self.assertEqual(w.load_state(self.run)["status"], "active")

    def payload(self, name, session="synthetic-session", **extra):
        return {"hook_event_name": name, "session_id": session, "cwd": str(self.base), **extra}

    def test_hooks_scope_pre_post_resume_stop_limit(self):
        self.assertEqual(h.handle(self.payload("Stop", session="different-session")), {})
        self.assertIn("additionalContext", h.handle(self.payload("SessionStart", source="compact"))["hookSpecificOutput"])
        for name in ("Bash", "exec_command", "functions.exec_command"):
            p = self.payload("PreToolUse", tool_name=name, tool_input={"command": f'python workflow.py finalize --run "{self.run}"'})
            self.assertEqual(h.handle(p)["hookSpecificOutput"]["permissionDecision"], "deny")
        p = self.payload("PreToolUse", tool_name="apply_patch", tool_input={"input": f"*** Update File: {self.run}/validation.json\n+passed"})
        self.assertEqual(h.handle(p)["hookSpecificOutput"]["permissionDecision"], "deny")
        h.handle(self.payload("PostToolUse", tool_name="Bash", tool_response={"exit_code": 0}))
        self.assertEqual(w.status(self.run)["nextStage"], "research_recorded")
        for _ in range(2):
            self.assertEqual(h.handle(self.payload("Stop", stop_hook_active=True))["decision"], "block")
        self.assertNotIn("decision", h.handle(self.payload("Stop", stop_hook_active=True)))
        state = w.load_state(self.run)
        state["status"] = "paused"
        w.save_state(self.run, state)
        self.assertNotIn("decision", h.handle(self.payload("Stop")))

    def test_ambiguous_binding_is_not_guessed(self):
        w.initialize(self.base/"synthetic-input.json", self.base/"synthetic-input.xlsx", self.base, session="synthetic-session")
        with self.assertRaisesRegex(Invalid, "multiple active"):
            h.handle(self.payload("Stop"))

    def test_hook_install_merges_and_does_not_trust(self):
        config = self.base/".codex"/"hooks.json"
        atomic(config, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing-hook"}]}]}})
        result = h.install(self.base, sys.executable)
        self.assertEqual(result["trusted"], "not_checked")
        self.assertEqual(read_json(config)["hooks"]["Stop"][0]["hooks"][0]["command"], "existing-hook")
        h.install(self.base, sys.executable)
        self.assertEqual(len(read_json(config)["hooks"]["Stop"]), 2)

    def test_pdf_gate_missing_browser_cannot_be_limited(self):
        self.advance_to_reports(2)
        with patch("generate_initial_screening_pdf.find_browser", side_effect=FileNotFoundError()):
            with self.assertRaises(Invalid):
                w.advance(self.run)
        self.assertEqual(read_json(self.run/"validation.json")["status"], "failed_validation")

    def test_pdf_timeout_fails_closed(self):
        self.advance_to_reports(2)
        with patch("generate_initial_screening_pdf.render_pdf", side_effect=subprocess.TimeoutExpired("synthetic-browser", 1)):
            with self.assertRaises(Invalid):
                w.advance(self.run)
        self.assertEqual(w.load_state(self.run)["checks"]["reports_validated"], "failed")
        self.assertEqual(read_json(self.run/"validation.json")["status"], "failed_validation")

    @unittest.skipUnless(os.name == "nt", "Windows hook runner command")
    def test_hook_command_via_native_cmd_stdin(self):
        h.install(self.base, sys.executable)
        command = read_json(self.base/".codex"/"hooks.json")["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        payload = json.dumps(self.payload("SessionStart", source="resume"))
        result = subprocess.run('cmd.exe /d /s /c "'+command+'"', input=payload, text=True, encoding="utf-8", capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
