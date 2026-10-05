"""A small file-backed workflow. Validators, not flags, authorize completion."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from extract_research_view import (Invalid, atomic, digest, encode, now, read_json,
                                  require, safe_text, validate_pair)
from validate_report import draft, eligible, matched_content, reports, validate_research

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0-rc.1"
STAGES = ("input_validated", "research_prepared", "research_recorded", "identity_classified", "analysis_validated", "reports_validated", "final_validated")
HELP = (
    "Validate immutable JSON/XLSX input and optional upstream receipt.",
    "Generate the temporary 26-field view and pending research.json.",
    "Fill research.json and sanitized, hashed observations for every customer; attempt identity and all four risk categories.",
    "Classify identities; matching requires company plus another source-backed anchor.",
    "Complete all nine dimensions, risk coverage and evidence-based/null scores.",
    "Generate reports, inspect every PDF page, then save visual-review.json bound to this PDF hash (see contract).",
    "Finalize: recheck all inputs, evidence, reports and PDF; delete only the temporary research view.")


def normalized(path):
    return os.path.normcase(str(Path(path).resolve()))


@contextmanager
def locked(path):
    """OS releases this advisory lock after a crash; no stale lock deletion."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except (BlockingIOError, PermissionError) as exc:
            if not acquired:
                raise Invalid("run is locked by another process; retry after it finishes") from exc
            raise
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def rules_hash():
    names = ["SKILL.md", "requirements.txt", *[p.relative_to(ROOT).as_posix() for directory, pattern in (("scripts", "*.py"), ("hooks", "*.py"), ("references", "*.md"), ("assets", "*.html")) for p in (ROOT/directory).glob(pattern)]]
    return hashlib.sha256(encode({n: digest(ROOT/n) for n in sorted(names)}).encode()).hexdigest()


def load_state(run):
    run = Path(run).resolve()
    state = read_json(run/"workflow_state.json")
    require(state.get("schemaVersion") == "alibaba.buyer-due-diligence.workflow.v1" and state.get("runId") == run.name and normalized(state.get("runPath", ".")) == normalized(run), "run binding mismatch")
    return state


def save_state(run, state):
    atomic(Path(run)/"workflow_state.json", state)


def event(run, kind, stage=None, code=0):
    # No tool arguments, input records, transcript, credentials or full error text.
    with (Path(run)/"events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": now(), "event": kind, "stage": stage, "exitCode": code})+"\n")


def inputs(state):
    for key in ("json", "xlsx", "phase"):
        spec = state["inputs"].get(key)
        if spec:
            require(digest(spec["path"]) == spec["sha256"], "locked input changed; create a new run with the corrected source pair")
    require(state["rulesSha256"] == rules_hash(), "rules changed; initialize a new run and revalidate")
    return validate_pair(state["inputs"]["json"]["path"], state["inputs"]["xlsx"]["path"], (state["inputs"].get("phase") or {}).get("path"))


def research(run, state, view, level=3):
    data = read_json(Path(run)/"research.json")
    return data, validate_research(data, view, state["inputs"]["json"]["sha256"], run, level)


def snapshot(run, state, names=()):
    return {"inputs": {k: v["sha256"] for k, v in state["inputs"].items() if v}, "rules": state["rulesSha256"], "files": {n: digest(Path(run)/n) for n in sorted(set(names))}}


def pdf_status(state, data):
    if not any(eligible(r) for r in data["records"]):
        return "skipped_no_matched_customers"
    if state["pdfPolicy"]["mode"] == "skip_user":
        require(state["pdfPolicy"].get("reason") and state["pdfPolicy"].get("approval"), "PDF skip requires explicit user approval and reason")
        return "skipped_by_user"
    return "passed"


def validate_products(run, state, view, data, summary):
    run = Path(run)
    names = set(summary["dependencies"])
    for name, content in reports(data, view, summary).items():
        require((run/name).read_text(encoding="utf-8") == content, name + ": differs from validated research")
        names.add(name)
    status = pdf_status(state, data)
    if status != "passed":
        require(not any((run/n).exists() for n in ("matched.pdf", "matched.content.json", "matched.html", "pdf-render.json", "visual-review.json")), "skipped PDF branch contains stale PDF artifacts")
        return names, status
    require(read_json(run/"matched.content.json") == matched_content(data, view), "matched content differs from qualified research")
    from generate_initial_screening_pdf import build_html, DEFAULT_TEMPLATE, validate_pdf
    require((run/"matched.html").read_text(encoding="utf-8") == build_html(DEFAULT_TEMPLATE, matched_content(data, view)), "rendered HTML mismatch")
    manifest = read_json(run/"pdf-render.json")
    pages = 1 + sum(eligible(r) for r in data["records"])
    require(manifest.get("contentSha256") == digest(run/"matched.content.json") and manifest.get("pdfSha256") == digest(run/"matched.pdf") and manifest.get("rulesSha256") == state["rulesSha256"] and manifest.get("pages") == pages, "PDF render proof mismatch")
    validate_pdf(run/"matched.pdf", pages, matched_content(data, view), digest(run/"matched.content.json"))
    review = read_json(run/"visual-review.json")
    require(review.get("pdfSha256") == digest(run/"matched.pdf") and review.get("status") == "passed" and review.get("pagesReviewed") == list(range(1, pages+1)) and review.get("issues") == [], "PDF visual review missing, stale or incomplete")
    from extract_research_view import timestamp, safety_walk
    timestamp(review.get("checkedAt"))
    safety_walk(review)
    require(isinstance(review.get("reviewer"), str) and review["reviewer"].strip() and isinstance(review.get("notes"), str) and review["notes"].strip(), "visual review needs reviewer and actual observations")
    names.update(("matched.content.json", "matched.html", "matched.pdf", "pdf-render.json", "visual-review.json"))
    return names, status


def check_stage(run, state, index, view=None):
    view = inputs(state) if view is None else view
    if index == 0:
        return snapshot(run, state)
    prepared = {"viewSha256": hashlib.sha256(encode(view).encode()).hexdigest(), "records": len(view["records"]), "sourceDate": view["sourceDate"]}
    require(read_json(Path(run)/"preparation.json") == prepared, "research preparation receipt mismatch")
    view_path = Path(run)/"research-view.tmp.json"
    if view_path.exists():
        require(read_json(view_path) == view, "temporary research view modified")
    if index == 1:
        return snapshot(run, state, ["preparation.json"])
    data, summary = research(run, state, view, min(index-1, 3))
    names = set(summary["dependencies"]) | {"preparation.json"}
    if index >= 5:
        products, _ = validate_products(run, state, view, data, summary)
        names.update(products)
    if index == 6:
        receipt = read_json(Path(run)/"validation.json")
        expected = receipt_fields(run, state, view, data, summary, names)
        require(all(receipt.get(k) == v for k, v in expected.items()), "completion receipt mismatch")
        require(not view_path.exists(), "temporary research view still present")
        names.add("validation.json")
    return snapshot(run, state, names)


def inspect(run, state=None):
    state = state or load_state(run)
    try:
        view = inputs(state)
    except Exception as exc:
        return 0, safe_error(exc)
    for i, stage in enumerate(STAGES):
        if stage not in state["proofs"]:
            return i, HELP[i]
        try:
            require(check_stage(run, state, i, view) == state["proofs"][stage], "artifact changed since stage validation")
        except Exception as exc:
            return i, safe_error(exc)
    return len(STAGES), "All local checks passed."


def safe_error(exc):
    # Never serialize an exception containing arbitrary customer values/paths.
    if isinstance(exc, Invalid):
        try:
            return safe_text(str(exc))[:500]
        except Invalid:
            pass
    if isinstance(exc, FileNotFoundError):
        basename = Path(exc.filename).name if exc.filename else ""
        if basename in {"visual-review.json", "research.json", "matched.pdf", "preparation.json"}:
            return "Required file missing: " + basename + "; follow the current stage contract."
        return "Required file missing; follow the current stage contract."
    return type(exc).__name__ + ": validation failed; inspect the local inputs without copying private values into logs."


def reconcile(run, state):
    index, reason = inspect(run, state)
    if index < len(STAGES):
        for stage in STAGES[index:]:
            state["proofs"].pop(stage, None)
            state["checks"][stage] = "pending"
        if state["status"] in {"completed", "completed_with_limitations"}:
            state["status"] = "active"
        if (Path(run)/"validation.json").exists():
            atomic(Path(run)/"validation.json", {"schemaVersion": "alibaba.buyer-due-diligence.validation.v1", "status": "failed_validation", "reason": reason, "nextStage": STAGES[index]})
    return index, reason


def receipt_fields(run, state, view, data, summary, names):
    return {"schemaVersion": "alibaba.buyer-due-diligence.validation.v1", "status": "completed_with_limitations" if summary["limitations"] else "completed",
            "version": VERSION, "runId": state["runId"], "sourceDate": view["sourceDate"], "inputSha256": state["inputs"]["json"]["sha256"],
            "rulesSha256": state["rulesSha256"], "customerCount": len(data["records"]), "matchedCount": sum(eligible(r) for r in data["records"]),
            "pdfStatus": pdf_status(state, data), "pdfPolicy": state["pdfPolicy"], "doubleSourceDimensions": summary["doubleSourceDimensions"], "dimensionDenominator": summary["dimensionDenominator"],
            "limitations": summary["limitations"], "upstreamPhase": "validated" if state["inputs"].get("phase") else "not_provided",
            "artifactSha256": {n: digest(Path(run)/n) for n in sorted(names)},
            "assurance": "Local structural/evidence checks; not proof of research truth, legal clearance or host hook activation."}


def generate_products(run, state, view, data, summary, browser=None):
    run = Path(run)
    for name, content in reports(data, view, summary).items():
        atomic(run/name, content)
    if pdf_status(state, data) != "passed":
        return
    content = matched_content(data, view)
    from generate_initial_screening_pdf import build_html, DEFAULT_TEMPLATE, find_browser, render_pdf, validate_pdf
    html = build_html(DEFAULT_TEMPLATE, content)
    reuse = False
    try:
        manifest = read_json(run/"pdf-render.json")
        reuse = (read_json(run/"matched.content.json") == content and manifest["contentSha256"] == digest(run/"matched.content.json") and manifest["pdfSha256"] == digest(run/"matched.pdf") and manifest["rulesSha256"] == state["rulesSha256"])
    except (OSError, ValueError, KeyError):
        pass
    if reuse:
        return
    atomic(run/"matched.content.json", content)
    atomic(run/"matched.html", html)
    pages = len(content["客户"])+1
    render_pdf(html, run/"matched.pdf", find_browser(browser), digest(run/"matched.content.json"))
    validate_pdf(run/"matched.pdf", pages, content, digest(run/"matched.content.json"))
    atomic(run/"pdf-render.json", {"contentSha256": digest(run/"matched.content.json"), "pdfSha256": digest(run/"matched.pdf"), "rulesSha256": state["rulesSha256"], "pages": pages, "checkedAt": now()})


def advance(run, finalize=False, browser=None):
    run = Path(run).resolve()
    with locked(run/".lock"):
        state = load_state(run)
        index, _ = reconcile(run, state)
        save_state(run, state)
        require(state["status"] in {"active", "completed", "completed_with_limitations"}, "run paused/cancelled/blocked; explicitly resume first")
        if index == len(STAGES):
            return status(run, state)
        stage = STAGES[index]
        try:
            if finalize:
                require(index == 6, "cannot finalize before " + stage)
            view = inputs(state)
            state["checks"][stage] = "running"
            save_state(run, state)
            if index == 1:
                atomic(run/"research-view.tmp.json", view)
                atomic(run/"preparation.json", {"viewSha256": hashlib.sha256(encode(view).encode()).hexdigest(), "records": len(view["records"]), "sourceDate": view["sourceDate"]})
                if not (run/"research.json").exists():
                    atomic(run/"research.json", draft(view, state["inputs"]["json"]["sha256"]))
            if index == 5:
                data, summary = research(run, state, view)
                generate_products(run, state, view, data, summary, browser)
            if index == 6:
                # Do not trust a hand-edited stage flag: all actual proofs are re-evaluated.
                for j in range(6):
                    require(state["proofs"].get(STAGES[j]) == check_stage(run, state, j, view), "prerequisite proof missing or changed")
                data, summary = research(run, state, view)
                products, _ = validate_products(run, state, view, data, summary)
                fields = receipt_fields(run, state, view, data, summary, set(products) | {"preparation.json"})
                # Exactly this generated temporary file, never the source or directory.
                require(not (run/"research-view.tmp.json").is_symlink(), "unsafe temporary view")
                (run/"research-view.tmp.json").unlink(missing_ok=True)
                atomic(run/"validation.json", {**fields, "checkedAt": now()})
                state["status"] = fields["status"]
            state["proofs"][stage] = check_stage(run, state, index, view)
            state["checks"][stage] = "limited" if (2 <= index <= 4 and research(run, state, view, min(index-1, 3))[1]["limitations"]) or (index == 6 and state["status"] == "completed_with_limitations") else "passed"
            state["lastError"] = None
            state["stopGuard"] = {"fingerprint": None, "attempts": 0}
            event(run, "stage_passed", stage)
        except Exception as exc:
            state["checks"][stage] = "failed"
            state["lastError"] = safe_error(exc)
            atomic(run/"validation.json", {"schemaVersion": "alibaba.buyer-due-diligence.validation.v1", "status": "failed_validation", "stage": stage, "reason": safe_error(exc)})
            event(run, "stage_failed", stage, 2)
            raise Invalid(safe_error(exc)) from exc
        finally:
            save_state(run, state)
    return status(run)


def initialize(input_path, xlsx_path, workspace, out=None, phase=None, session=None, skip_reason=None, skip_approval=None):
    view = validate_pair(input_path, xlsx_path, phase)
    workspace = Path(workspace).resolve()
    require(workspace.is_dir(), "workspace must exist")
    require(bool(skip_reason) == bool(skip_approval), "PDF skip requires reason and explicit user approval")
    if skip_reason:
        safe_text(skip_reason)
        safe_text(skip_approval)
    run_id = uuid.uuid4().hex[:16]
    run = (Path(out).resolve() if out else workspace/"outputs") / view["sourceDate"] / run_id
    run.mkdir(parents=True, exist_ok=False)
    state = {"schemaVersion": "alibaba.buyer-due-diligence.workflow.v1", "version": VERSION, "runId": run_id, "runPath": str(run), "workspace": normalized(workspace), "sessionId": session,
        "status": "active", "createdAt": now(), "rulesSha256": rules_hash(),
        "inputs": {k: {"path": str(Path(p).resolve()), "sha256": digest(p)} if p else None for k, p in (("json", input_path), ("xlsx", xlsx_path), ("phase", phase))},
        "pdfPolicy": {"mode": "skip_user" if skip_reason else "required_when_matched", "reason": skip_reason, "approval": skip_approval},
        "proofs": {}, "checks": {s: "pending" for s in STAGES}, "lastError": None, "stopGuard": {"fingerprint": None, "attempts": 0}, "hookEventsObserved": []}
    save_state(run, state)
    event(run, "initialized")
    if session:
        registry = workspace/".codex"/"alibaba-due-diligence.bindings.json"
        with locked(registry.with_suffix(".lock")):
            bindings = read_json(registry) if registry.exists() else []
            bindings.append({"sessionId": session, "workspace": normalized(workspace), "runId": run_id, "runPath": str(run)})
            atomic(registry, bindings)
    return status(run)


def status(run, state=None):
    state = state or load_state(run)
    i, reason = inspect(run, state)
    return {"run": str(Path(run).resolve()), "status": state["status"] if i == 7 or state["status"] in {"paused", "blocked", "cancelled"} else "active", "complete": i == 7,
            "nextStage": STAGES[i] if i < 7 else None, "instruction": HELP[i] if i < 7 else "Done", "detail": state.get("lastError") or reason,
            "checks": {s: state["checks"].get(s, "pending") if j <= i else "pending" for j, s in enumerate(STAGES)},
            "hookEventsObserved": state.get("hookEventsObserved", []), "hookActivation": "not_certified; native trust and live probes are separate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--input", required=True, type=Path)
    init.add_argument("--xlsx", required=True, type=Path)
    init.add_argument("--workspace", type=Path, default=Path.cwd())
    init.add_argument("--out", type=Path, help="Output base; a date/runId directory is always added")
    init.add_argument("--phase-status", type=Path)
    init.add_argument("--session-id", default=os.environ.get("CODEX_THREAD_ID"))
    init.add_argument("--skip-pdf-reason")
    init.add_argument("--skip-pdf-approval", help="Exact user instruction authorizing this skip")
    for name in ("next", "status", "advance", "finalize", "pause", "block", "cancel", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True, type=Path)
        if name == "advance":
            p.add_argument("--chrome")
        if name in {"pause", "block", "cancel", "resume"}:
            p.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.input, args.xlsx, args.workspace, args.out, args.phase_status, args.session_id, args.skip_pdf_reason, args.skip_pdf_approval)
        elif args.command in {"advance", "finalize"}:
            result = advance(args.run, args.command == "finalize", getattr(args, "chrome", None))
        elif args.command in {"status", "next"}:
            result = status(args.run)
        else:
            safe_text(args.reason)
            require(args.reason.strip(), "reason required")
            with locked(args.run/".lock"):
                state = load_state(args.run)
                state["status"] = {"pause": "paused", "block": "blocked", "cancel": "cancelled", "resume": "active"}[args.command]
                state["lifecycleReason"] = args.reason
                state["stopGuard"] = {"fingerprint": None, "attempts": 0}
                save_state(args.run, state)
                event(args.run, args.command)
            result = status(args.run)
        print(encode(result))
        return 0
    except Exception as exc:
        print(encode({"status": "failed_validation", "error": safe_error(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
