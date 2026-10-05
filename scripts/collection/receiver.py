"""Loopback-only collection bridge. No browser launch, credentials export or upload."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from extract_research_view import Invalid, atomic, encode, now, read_json, require, safety_walk
from . import collect
from .export_source import index_by_customer_id, read_jsonl, sanitize_private_source

MAX_BODY = 32 * 1024 * 1024
ORIGINS = {"list": {"https://alicrm.alibaba.com", "https://i.alibaba.com"},
           "detail": {"https://alicrm.alibaba.com", "https://i.alibaba.com"},
           "profile": {"https://profile.alibaba.com"}}


class Bridge:
    def __init__(self, run, state, kind, resume=False):
        self.run, self.state, self.kind = Path(run), state, kind
        self.base = collect.folder(run)
        self.secret, self.started = secrets.token_urlsafe(32), now()
        self.deadline = time.monotonic() + 1800
        self.stopped, self.closed = False, False
        self.inflight = None
        failure_log = self.base/"collector_errors.jsonl"
        require(resume or not failure_log.exists() or not any(e.get("kind") == kind for e in read_jsonl(failure_log)), "browser failure requires session review and --resume-after-review")
        listing, _, _ = collect.paths(run, state)
        if kind == "list":
            require(not (self.base/"list.wrapper.json").exists(), "list already saved; advance instead of recollecting")
            self.queue, self.done = {}, {}
        else:
            rows = read_json(listing)["records"] if kind == "detail" else collect.profile_queue(read_jsonl(self.base/"detail_missing_fields.jsonl"))
            self.queue = index_by_customer_id(rows, kind)
            require(self.queue, "no queued rows; advance the explicit no-link branch")
            data_name, _, errors_name = collect.filenames(kind)
            data = self.base/data_name
            self.done = index_by_customer_id(read_jsonl(data), kind) if data.exists() else {}
            require(set(self.done) <= set(self.queue), "saved rows belong to another queue")
            errors_path = self.base/errors_name
            errors = read_jsonl(errors_path) if errors_path.exists() else []
            require(resume or all(e.get("customerId") in self.done for e in errors), "prior failure requires session/risk review and --resume-after-review")
            if not data.exists():
                atomic(data, "")
            if not errors_path.exists():
                atomic(errors_path, "")
            self.progress()

    def progress(self):
        if self.kind == "list":
            return {"doneCount": int((self.base/"list.wrapper.json").exists()), "stopped": self.stopped}
        remaining = len(self.queue)-len(self.done)
        failed = int(self.stopped and remaining > 0)
        counts = {"runId": self.state["runId"], "kind": self.kind, "total": len(self.queue),
                  "doneCount": len(self.done), "failedCount": failed,
                  "pendingCount": remaining-failed, "updatedAt": now()}
        atomic(self.base/collect.filenames(self.kind)[1], counts)
        return counts

    def next_row(self):
        require(not self.stopped, "collector stopped after a failure; inspect session before resuming")
        if self.inflight:
            require(time.monotonic() - self.inflight[2] < 600, "batch expired; restart after verifying the session")
            return {"lease": self.inflight[0], "rows": [self.queue[self.inflight[1]]]}
        pending = [cid for cid in self.queue if cid not in self.done]
        if not pending:
            return {"lease": None, "rows": []}
        self.inflight = (secrets.token_hex(16), pending[0], time.monotonic())
        return {"lease": self.inflight[0], "rows": [self.queue[pending[0]]]}

    def accept(self, body):
        require(not self.stopped, "collector is stopped; session review required")
        require(body.get("runId") == self.state["runId"] and body.get("kind") == self.kind, "cross-run result rejected")
        if self.kind == "list":
            require(not (self.base/"list.wrapper.json").exists(), "duplicate list rejected")
            value = sanitize_private_source(body.get("value"))
            collect.wrapper_check(value, self.state)
            atomic(self.base/"list.wrapper.json", value)
            return
        require(self.inflight and body.get("lease") == self.inflight[0] and time.monotonic()-self.inflight[2] < 600, "unissued, duplicate or expired batch")
        rows = body.get("results")
        require(isinstance(rows, list) and len(rows) == 1 and rows[0].get("customerId") == self.inflight[1], "result does not match issued queue")
        row = sanitize_private_source(rows[0])
        cid = row["customerId"]
        data_name, _, errors_name = collect.filenames(self.kind)
        if row.get("error"):
            # Do not persist exception strings that may contain private URLs or transport material.
            self.append(errors_name, {"customerId": cid, "error": "collection_failed_review_login_risk_or_page_format", "at": now()})
            self.stopped = True
        else:
            require(cid not in self.done, "duplicate result rejected")
            if self.kind == "detail":
                require(isinstance(row.get("fieldsMissingFromList"), dict) and isinstance(row["fieldsMissingFromList"].get("contacts"), list), "malformed detail result")
            else:
                link = self.queue[cid]["buyerProfileLink"]
                require(row.get("buyerProfileLink") == row.get("profileUrl") == link and isinstance(row.get("buyerProfile"), dict), "profile identity/locator mismatch")
            safety_walk(row, private=True)
            self.append(data_name, row)
            self.done[cid] = row
        self.inflight = None
        self.progress()

    def append(self, name, value):
        target = self.base/name
        require(not target.is_symlink(), "refusing symlink output")
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def code(self, port):
        source = (collect.ROOT/(self.kind+".browser.js")).read_text(encoding="utf-8")
        config = self.state["collection"]
        options = {"startDate": config["targetDate"], "endDate": config["endDate"], "salesName": "全部"}
        if config.get("salesMap"):
            options["salesSegments"] = read_json(config["salesMap"]["path"])["segments"]
        settings = {"origin": f"http://127.0.0.1:{port}", "credential": self.secret,
                    "runId": self.state["runId"], "kind": self.kind, "options": options}
        worker = (collect.ROOT/"worker.js").read_text(encoding="utf-8")
        return "(async () => {\n"+source+"\nconst settings = "+json.dumps(settings)+";\n"+worker+"\n})()"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log request URLs, credentials, payloads or browser locators.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, value, javascript=False):
        data = value.encode() if javascript else encode(value).encode()
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin in ORIGINS[self.server.bridge.kind]:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/javascript; charset=utf-8" if javascript else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def check(self, preflight=False):
        bridge = self.server.bridge
        require(self.client_address[0] == "127.0.0.1" and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}", "loopback host required")
        origin = self.headers.get("Origin")
        require(origin in ORIGINS[bridge.kind] or (not origin and self.path in {"/status", "/profile-url", "/shutdown"}), "origin rejected")
        require(time.monotonic() < bridge.deadline, "receiver expired; restart after session review")
        if preflight:
            return
        require(hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer "+bridge.secret), "access rejected")
        import workflow
        current = workflow.load_state(bridge.run)
        require(current["runId"] == bridge.state["runId"], "run binding changed")
        if self.path not in {"/status", "/shutdown"}:
            require(current["status"] == "active", "run is not active")
            require(current["rulesSha256"] == workflow.rules_hash(), "rules changed; restart a new run")
            required = {"list": 0, "detail": 1, "profile": 3}[bridge.kind]
            require(workflow.inspect(bridge.run, current)[0] == required, "receiver stage is stale")

    def do_OPTIONS(self):
        try:
            self.check(preflight=True)
            require(self.headers.get("Access-Control-Request-Method") in {"GET", "POST"}, "method rejected")
            requested = {x.strip().lower() for x in self.headers.get("Access-Control-Request-Headers", "").split(",") if x.strip()}
            require(requested <= {"authorization", "content-type"}, "headers rejected")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Vary", "Origin")
            self.end_headers()
        except Exception:
            self.reply(403, {"error": "preflight rejected"})

    def do_GET(self):
        try:
            self.check()
            b = self.server.bridge
            if self.path == "/collector.js":
                self.reply(200, b.code(self.server.server_port), True)
            elif self.path == "/status":
                self.reply(200, {**self.server.identity, "doneCount": len(b.done), "stopped": b.stopped})
            elif self.path == "/profile-url" and b.kind == "profile":
                self.reply(200, {"url": collect.profile_url(next(iter(b.queue.values()))["buyerProfileLink"])})
            else:
                self.reply(404, {"error": "not found"})
        except Exception:
            self.reply(403, {"error": "request rejected"})

    def do_POST(self):
        try:
            self.check()
            require(self.headers.get("Content-Type", "").split(";")[0] == "application/json" and not self.headers.get("Transfer-Encoding"), "JSON body required")
            size = int(self.headers.get("Content-Length", "0"))
            require(0 < size <= MAX_BODY, "invalid body size")
            raw = self.rfile.read(size)
            require(len(raw) == size, "incomplete body")
            body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite value")))
            require(isinstance(body, dict), "body must be an object")
            b = self.server.bridge
            require(body.get("runId") == b.state["runId"], "cross-run request")
            if self.path == "/next" and b.kind != "list":
                self.reply(200, b.next_row())
            elif self.path == "/results":
                b.accept(body)
                self.reply(200, {"saved": True})
            elif self.path == "/failure":
                b.append("collector_errors.jsonl", {"at": now(), "kind": b.kind, "error": "browser_collection_failed"})
                b.stopped = True
                self.reply(200, {"stopped": True})
            elif self.path == "/shutdown" and body.get("startedAt") == b.started and body.get("pid") == os.getpid():
                b.closed = True
                self.reply(200, {"stopped": True})
            else:
                self.reply(404, {"error": "not found"})
        except Exception:
            self.reply(400, {"error": "invalid or unauthorized collection request"})


def serve(run, kind, port, resume=False):
    import workflow
    run = Path(run).resolve()
    require(1024 <= port <= 65535, "invalid loopback port")
    # ponytail: serial batches and full prerequisite rechecks are O(n²) I/O; cache validated snapshots only if large live batches warrant it.
    with workflow.locked(run/".receiver.lock"):
        state = workflow.load_state(run)
        require(state.get("collection") and state["status"] == "active", "active collection run required")
        require(workflow.inspect(run, state)[0] == {"list": 0, "detail": 1, "profile": 3}[kind], "wrong receiver stage")
        with HTTPServer(("127.0.0.1", port), Handler) as server:
            server.timeout = 1
            server.bridge = b = Bridge(run, state, kind, resume)
            identity = {"pid": os.getpid(), "startedAt": b.started, "runId": state["runId"], "run": str(run), "kind": kind, "port": port}
            server.identity = identity
            snippet_path = b.base/("inject-"+kind+".js")
            function_path = b.base/("inject-"+kind+".devtools.js")
            auth_path = b.base/("receiver-"+kind+".private.json")
            snippet = "(async()=>{const r=await fetch("+json.dumps(f"http://127.0.0.1:{port}/collector.js")+",{headers:{Authorization:"+json.dumps("Bearer "+b.secret)+"}});if(!r.ok)throw Error('Receiver unavailable');return eval(await r.text());})()"
            atomic(snippet_path, snippet)
            # DevTools evaluate_script accepts a function, while manual Console accepts the expression.
            atomic(function_path, "async () => { return " + snippet + "; }")
            atomic(b.base/("receiver-"+kind+".json"), identity)
            atomic(auth_path, {**identity, "credential": b.secret})  # Publish readiness after both payload files exist.
            print(encode({**identity, "snippetFile": str(snippet_path), "devtoolsFunctionFile": str(function_path), "note": "Execute privately in the authorized page context; never print/commit the snippet, function or auth file."}), flush=True)
            try:
                while not b.closed and time.monotonic() < b.deadline:
                    server.handle_request()
            finally:
                # Remove only this receiver's ephemeral secret files, preserve all data and audit records.
                for path in (snippet_path, function_path, auth_path):
                    require(not path.is_symlink(), "unsafe credential cleanup path")
                    path.unlink(missing_ok=True)
    return 0


def control(run, kind, stop=False):
    """Authenticate and compare the complete saved identity before stopping; never kill PIDs."""
    from urllib.request import Request, urlopen
    base = collect.folder(run)
    saved = read_json(base/("receiver-"+kind+".private.json"))
    require(saved["run"] == str(Path(run).resolve()) and saved["runId"] == Path(run).name and saved["kind"] == kind, "receiver binding mismatch")
    headers = {"Authorization": "Bearer "+saved["credential"]}
    origin = "http://127.0.0.1:"+str(saved["port"])
    with urlopen(Request(origin+"/status", headers=headers), timeout=3) as response:
        actual = json.load(response)
    require(all(actual.get(k) == saved.get(k) for k in ("pid", "startedAt", "runId", "run", "kind", "port")), "receiver identity mismatch; do not reuse or terminate")
    if stop:
        headers["Content-Type"] = "application/json"
        body = json.dumps({k: saved[k] for k in ("pid", "startedAt", "runId")}).encode()
        with urlopen(Request(origin+"/shutdown", data=body, headers=headers), timeout=3) as response:
            require(json.load(response).get("stopped") is True, "receiver did not confirm stop")
        return {"stopped": True, "kind": kind, "runId": saved["runId"]}
    return actual
