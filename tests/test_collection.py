"""Synthetic collection -> shared workflow; no Alibaba access or real customer fixture."""
import copy
from http.server import HTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts"), str(ROOT/"examples"), str(ROOT/"hooks")]
import workflow as w
import codex_guard
from collection import collect as c
from collection.receiver import Bridge, Handler, control
from extract_research_view import Invalid, atomic, digest, now, read_json, validate_pair
from synthetic_demo import fill, source


def wrapper(count=3):
    rows = [{"customerId": r["crmCustomerId"], "companyName": r["customerDetail"]["companyName"],
             "loginId": r["buyerLoginId"]} for r in source(count)["records"]]
    start = int(c.datetime(2026, 10, 4, tzinfo=c.timezone(c.timedelta(hours=8))).timestamp())
    return {"status": 200, "ok": True, "capturedAt": now(),
            "criteria": {"startDate": "2026-10-04", "endDate": "2026-10-05", "salesName": "全部", "salesId": None,
                         "pageSize": 500, "total": count, "recordsCollected": count},
            "requestBody": {"jsonArray": json.dumps([{"id": "667", "gmt_create_start": str(start), "gmt_create_end": str(start+86400)}])},
            "json": {"success": True, "data": {"total": count, "data": rows}}}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        config = c.configuration("2026-10-04")
        self.run = Path(w.initialize(None, None, self.base, collect_config=config, session="synthetic-collection")["run"])
        self.state = w.load_state(self.run)

    def tearDown(self):
        self.temp.cleanup()

    def save_list(self, count=3):
        b = Bridge(self.run, self.state, "list")
        b.accept({"runId": self.run.name, "kind": "list", "value": wrapper(count)})
        return w.advance(self.run)

    def test_collection_to_research_full_flow_and_source_tamper(self):
        self.assertEqual(self.save_list()["nextStage"], "collection.customer_detail_done")
        with self.assertRaises(Invalid):
            w.advance(self.run, finalize=True)
        records = source()["records"]
        for i, r in enumerate(records[:2]):
            # Preserve the exact private locator; normalization is navigation-only.
            link = f" http://profile.alibaba.com/profile/my_profile.htm?m=synthetic-{i}#anchor "
            r["customerDetail"]["fieldsMissingFromList"]["contacts"][0]["buyerProfileLink"] = link
            r["buyerHomepage"].update(buyerProfileLink=link, profileUrl=link)
        bridge = Bridge(self.run, w.load_state(self.run), "detail")
        for i, record in enumerate(records):
            batch = bridge.next_row()
            cid = batch["rows"][0]["customerId"]
            self.assertEqual(cid, record["crmCustomerId"])
            bridge.accept({"runId": self.run.name, "kind": "detail", "lease": batch["lease"], "results": [record["customerDetail"]]})
            if i == 0:
                bridge = Bridge(self.run, w.load_state(self.run), "detail")
                self.assertEqual(len(bridge.done), 1)
        self.assertEqual(bridge.next_row()["rows"], [])
        w.advance(self.run)
        w.advance(self.run)
        bridge = Bridge(self.run, w.load_state(self.run), "profile")
        for record in records[:2]:
            batch = bridge.next_row()
            bridge.accept({"runId": self.run.name, "kind": "profile", "lease": batch["lease"], "results": [record["buyerHomepage"]]})
        for _ in range(3):
            result = w.advance(self.run)
        self.assertEqual(result["nextStage"], "research.input_validated")
        self.assertEqual(c.diagnose(self.run, devtools_authorized=True, host_allows_devtools=True)["code"], "COLLECTION_COMPLETE")
        state = w.load_state(self.run)
        self.assertEqual(len(validate_pair(state["inputs"]["json"]["path"], state["inputs"]["xlsx"]["path"])["records"]), 3)
        full = read_json(state["inputs"]["json"]["path"])
        self.assertEqual(full["records"][0]["buyerHomepage"]["profileUrl"], records[0]["buyerHomepage"]["profileUrl"])
        w.advance(self.run)
        w.advance(self.run)
        fill(self.run, 0)
        for _ in range(5):
            result = w.advance(self.run)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["checks"]), 13)
        self.assertNotIn("profile.alibaba.com", (self.run/"report.md").read_text(encoding="utf-8"))
        data = c.folder(self.run)/"detail_missing_fields.jsonl"
        with data.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(records[0]["customerDetail"])+"\n")
        self.assertFalse(w.status(self.run)["complete"])
        self.assertEqual(w.status(self.run)["nextStage"], "collection.customer_detail_done")

    def test_zero_customers_is_not_six_fake_passes(self):
        result = self.save_list(0)
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "completed_no_customers")
        self.assertEqual(result["checks"]["collection.customer_detail_done"], "not_applicable_no_customers")
        self.assertFalse((self.run/"research.json").exists())
        self.assertFalse((self.run/"matched.pdf").exists())
        value = read_json(c.folder(self.run)/"list.wrapper.json")
        value["json"]["success"] = False
        atomic(c.folder(self.run)/"list.wrapper.json", value)
        self.assertFalse(w.status(self.run)["complete"])

    def test_nonempty_no_profile_links_still_exports_pair(self):
        self.save_list(1)
        bridge = Bridge(self.run, w.load_state(self.run), "detail")
        batch = bridge.next_row()
        bridge.accept({"runId": self.run.name, "kind": "detail", "lease": batch["lease"],
                       "results": [source(1)["records"][0]["customerDetail"]]})
        for _ in range(5):
            result = w.advance(self.run)
        self.assertEqual(result["nextStage"], "research.input_validated")
        proof = w.load_state(self.run)["proofs"]["collection.buyer_profile_done"]
        self.assertEqual(proof["summary"]["buyerProfileStatus"], "skipped_no_profile_links")
        self.assertFalse(result["complete"])
        self.assertTrue(c.paths(self.run, self.state)[2].exists())

    def test_invalid_empty_dates_filters_counts_and_duplicate_rejected(self):
        mutations = [
            lambda v: v.update(status=403),
            lambda v: v["json"].update(success=False),
            lambda v: v["json"]["data"].pop("total"),
            lambda v: v["criteria"].update(startDate="2026-10-03"),
            lambda v: v["criteria"].update(recordsCollected=4),
            lambda v: v["requestBody"].update(jsonArray="[]"),
            lambda v: v["json"]["data"]["data"].__setitem__(1, v["json"]["data"]["data"][0]),
        ]
        for mutation in mutations:
            value = wrapper()
            mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises((Invalid, ValueError)):
                c.wrapper_check(value, self.state)

    def test_cross_run_wrong_identity_duplicate_and_reviewed_resume(self):
        self.save_list()
        b = Bridge(self.run, w.load_state(self.run), "detail")
        batch = b.next_row()
        payload = {"runId": self.run.name, "kind": "detail", "lease": batch["lease"], "results": [source()["records"][0]["customerDetail"]]}
        for mutation in (lambda p: p.update(runId="different"), lambda p: p.update(lease="unissued"),
                         lambda p: p["results"][0].update(customerId="unknown")):
            wrong = copy.deepcopy(payload)
            mutation(wrong)
            with self.assertRaises(Invalid):
                b.accept(wrong)
        cid = batch["rows"][0]["customerId"]
        b.accept({**payload, "results": [{"customerId": cid, "error": "secret must never be written"}]})
        with self.assertRaises(Invalid):
            b.next_row()
        with self.assertRaises(Invalid):
            Bridge(self.run, w.load_state(self.run), "detail")
        resumed = Bridge(self.run, w.load_state(self.run), "detail", resume=True)
        batch = resumed.next_row()
        resumed.accept({**payload, "lease": batch["lease"]})
        with self.assertRaises(Invalid):
            resumed.accept({**payload, "lease": batch["lease"]})
        self.assertNotIn("secret must", (c.folder(self.run)/"errors.jsonl").read_text())

    def test_http_origin_credential_size_identity_and_malformed_json(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.bridge = b = Bridge(self.run, self.state, "list")
        server.identity = {"runId": self.run.name, "kind": "list"}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, origin="https://alicrm.alibaba.com", credential=None, headers=None):
            options = {"Origin": origin, "Authorization": "Bearer "+(credential or b.secret), **(headers or {})}
            if body is not None:
                options["Content-Type"] = "application/json"
            return urlopen(Request(f"http://127.0.0.1:{server.server_port}"+path, data=body, headers=options), timeout=5)
        try:
            with request("/collector.js") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://alicrm.alibaba.com")
                self.assertNotEqual(response.headers["Access-Control-Allow-Origin"], "*")
            for origin, credential in [("https://evil.example", None), ("https://alicrm.alibaba.com", "wrong")]:
                with self.assertRaises(HTTPError):
                    request("/collector.js", origin=origin, credential=credential)
            with self.assertRaises(HTTPError):
                request("/results", b"{")
            with self.assertRaises(HTTPError):
                request("/results", b"{}", headers={"Content-Length": str(33*1024*1024)})
            value = json.dumps({"runId": self.run.name, "kind": "list", "value": wrapper(0)}).encode()
            with request("/results", value) as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(HTTPError):
                request("/results", value)
            self.assertTrue(w.advance(self.run)["complete"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_collection_hooks_block_research_shortcuts(self):
        payload = {"hook_event_name": "PreToolUse", "session_id": "synthetic-collection", "cwd": str(self.base),
                   "tool_name": "exec_command", "tool_input": {"cmd": f'python workflow.py finalize --run "{self.run}"'}}
        self.assertEqual(codex_guard.handle(payload)["hookSpecificOutput"]["permissionDecision"], "deny")
        payload.update(hook_event_name="Stop")
        for _ in range(2):
            self.assertEqual(codex_guard.handle(payload)["decision"], "block")
        self.assertNotIn("decision", codex_guard.handle(payload))

    def test_browser_collectors_offline(self):
        result = subprocess.run([shutil.which("node"), str(ROOT/"tests"/"collection_browser.cjs")], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_diagnosis_is_read_only_and_site_gates_take_priority(self):
        before = digest(self.run/"workflow_state.json")
        self.assertEqual(c.diagnose(self.run, ["devtools_failed"])["code"], "DEVTOOLS_CONNECTION_FAILED")
        self.assertEqual(c.diagnose(self.run, ["devtools_failed", "captcha"])["code"], "SITE_GATE_BLOCKED")
        self.assertEqual(c.diagnose(self.run, ["cim_query_failed"])["code"], "PROCESS_QUERY_FAILED")
        self.assertEqual(before, digest(self.run/"workflow_state.json"))
        self.assertFalse(c.diagnose(self.run, ["unknown_failure"])["automaticRetry"])

    def test_beijing_calendar_and_mapping_input(self):
        self.assertEqual(c.configuration("2025-12-31")["endDate"], "2026-01-01")
        self.assertEqual(c.configuration("2024-02-29")["endDate"], "2024-03-01")
        with self.assertRaises(ValueError):
            c.configuration("2026-02-30")
        with self.assertRaises(Invalid):
            c.configuration("20261004")
        mapping = self.base/"synthetic-map.json"
        atomic(mapping, {"verifiedAt": now(), "includesUnassigned": False, "segments": [{"salesId": "synthetic"}]})
        with self.assertRaises(Invalid):
            c.configuration("2026-10-04", sales_map=mapping)

    def test_real_loopback_process_identity_and_clean_shutdown(self):
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # Windows venv executables can be redirectors with a separate child PID.
        process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-X", "utf8", str(ROOT/"scripts"/"workflow.py"),
                                    "receiver", "--run", str(self.run), "--kind", "list", "--port", str(port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            marker = c.folder(self.run)/"receiver-list.private.json"
            deadline = time.monotonic()+8
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            function_path = c.folder(self.run)/"inject-list.devtools.js"
            self.assertTrue(function_path.exists())
            # Execute the generated function against a fake fetch, without any browser/network.
            js = "const fs=require('fs'),vm=require('vm'); const f=vm.runInNewContext('('+fs.readFileSync(process.argv[1],'utf8')+')',{fetch:async(u,o)=>{if(!o.headers.Authorization.startsWith('Bearer '))throw Error('auth missing');return {ok:true,text:async()=>'(async()=>({started:true}))()'}}}); f().then(r=>{if(!r.started)process.exit(2)}).catch(()=>process.exit(3));"
            checked = subprocess.run([shutil.which("node"), "-e", js, str(function_path)], capture_output=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(control(self.run, "list")["pid"], process.pid)
            self.assertTrue(control(self.run, "list", stop=True)["stopped"])
            _, errors = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, errors.decode())
            self.assertFalse(marker.exists())
            self.assertFalse((c.folder(self.run)/"inject-list.js").exists())
            self.assertFalse(function_path.exists())
        finally:
            if process.poll() is None:
                try:
                    control(self.run, "list", stop=True)
                    process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.terminate()  # Exact base-interpreter handle owned by this test.
                        process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
