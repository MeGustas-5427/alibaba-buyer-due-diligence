"""Collection gates for the shared workflow, not a second state machine."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit

from extract_research_view import atomic, digest, now, read_json, require, safety_walk, timestamp, validate_pair, write_workbook
from .export_source import build_payload, extract_source_date, index_by_customer_id, list_identity_map, read_jsonl, sanitize_private_source

ROOT = Path(__file__).resolve().parent
PHASES = ("input_resolved", "customer_detail_done", "profile_links_resolved",
          "buyer_profile_done", "nanyue_export_done", "final_validated")
HELP = (
    "Connect through native Chrome DevTools list_pages and verify the CRM page first; no extension/browser-client route. Run receiver --kind list, execute devtoolsFunctionFile through native evaluate_script, then advance to validate and enrich the wrapper.",
    "Run receiver --kind detail and execute devtoolsFunctionFile through native Chrome DevTools in the verified CRM page; require all rows saved, then advance.",
    "Advance to account for every profile link and every customer without a link.",
    "If profile links exist, use native Chrome DevTools in profile.alibaba.com to execute devtoolsFunctionFile from receiver --kind profile; otherwise advance records the no-link branch.",
    "Advance to export and validate the private v3 JSON/XLSX pair.",
    "Advance to revalidate all collection artifacts and bind this exact pair to research.")
SITE_STOPS = ("login_required", "captcha", "session_expired", "risk_control", "api_probe_failed", "profile_not_loaded")
SIGNALS = ("cim_query_failed", "process_query_failed", "ahk_identity_unverified", "devtools_failed",
           "receiver_identity_mismatch", "unknown_failure", *SITE_STOPS)


def unresolved_errors(run):
    """Successful rows can resolve old failures; never erase errors to enable recovery."""
    unresolved = 0
    complete_kinds = set()
    if (folder(run)/"list.wrapper.json").exists():
        complete_kinds.add("list")
    for kind in ("detail", "profile"):
        data, progress_name, error = filenames(kind)
        saved = {r.get("customerId") for r in read_jsonl(folder(run)/data)} if (folder(run)/data).exists() else set()
        errors = read_jsonl(folder(run)/error) if (folder(run)/error).exists() else []
        unresolved += sum(not r.get("customerId") or r["customerId"] not in saved for r in errors)
        progress = read_json(folder(run)/progress_name) if (folder(run)/progress_name).exists() else {}
        if progress.get("total") == progress.get("doneCount") == len(saved) and progress.get("failedCount") == progress.get("pendingCount") == 0:
            complete_kinds.add(kind)
    failure_log = folder(run)/"collector_errors.jsonl"
    if failure_log.exists():
        unresolved += sum(r.get("kind") not in complete_kinds for r in read_jsonl(failure_log))
    return unresolved


def diagnose(run, signals=(), *, devtools_authorized=False, host_allows_devtools=False, ahk_authorized=False):
    """Adapted from daily_diagnostics: observations are not permissions or site-state proof."""
    import workflow
    require(all(s in SIGNALS for s in signals), "unknown diagnostic signal")
    state = workflow.load_state(run)
    current = workflow.status(run, state)
    site = next((s for s in SITE_STOPS if s in signals), None)
    status, scope, checks = "stop", "read_only_diagnosis", []
    if site:
        code, action = "SITE_GATE_BLOCKED", "Stop collection. Resolve the observed login/CAPTCHA/risk/API/page issue in the same authorized session; never bypass it."
    elif "receiver_identity_mismatch" in signals:
        code, action = "RECEIVER_IDENTITY_MISMATCH", "Do not reuse or terminate this receiver. Compare the saved run/kind/port/PID/start identity through receiver-status."
    elif "unknown_failure" in signals:
        code, action = "UNKNOWN_FAILURE", "Preserve the original operation/error and perform bounded read-only diagnosis; do not infer login failure or zero customers."
    elif "ahk_identity_unverified" in signals:
        code, action = "AHK_IDENTITY_UNVERIFIED", "Do not reuse, terminate, replace or launch a helper until its exact script and current-run process identity are verified."
    elif state["rulesSha256"] != workflow.rules_hash():
        code, action = "RULES_CHANGED", "Preserve this run. Initialize a new run under the current rules; do not edit its stored hash."
    elif state["status"] in {"paused", "cancelled", "blocked"}:
        code, action = "RUN_NOT_ACTIVE", "Read the lifecycle reason and resolve it before an explicit resume. Read-only diagnosis does not resume a run."
    elif state.get("lastError") or (current["nextStage"] in state["proofs"]):
        code, action = "VALIDATOR_FAILED", current["detail"]
    elif state.get("collection") and unresolved_errors(run):
        code, action = "UNRESOLVED_COLLECTION_ERRORS", "Inspect saved errors and the live session before reviewed pending-only resume; do not clear errors or repeat successful customers."
    elif current["complete"]:
        code, action, status = "RUN_COMPLETE", "No re-collection or repeated browser connection is needed for this completed run.", "complete"
    elif not state.get("collection"):
        code, action, status = "FILE_INPUT_MODE", current["instruction"], "check"
    elif current["nextStage"].startswith("research."):
        code, action, status = "COLLECTION_COMPLETE", "Collection is already validated. Continue the current research stage; do not reconnect or recollect.", "check"
    elif "devtools_failed" in signals:
        code, action = "DEVTOOLS_CONNECTION_FAILED", "The native connection itself failed. Inspect its original error and native prompt; no automatic retry, prompt click, browser restart or channel switch."
    elif (devtools_authorized or ahk_authorized) and not host_allows_devtools:
        code, action = "HOST_CHANNEL_NOT_ALLOWED", "The required native Chrome DevTools channel is not permitted by the applicable host rules. Stop and report the conflict; do not change global policy or fall back to extensions, another browser or manual Console."
    elif (host_allows_devtools or ahk_authorized) and not devtools_authorized:
        code, action = "REVIEW_BROWSER_AUTHORIZATION", "Obtain explicit current-task authorization for the native Chrome DevTools channel. Earlier automations and this flag do not grant it."
    elif "cim_query_failed" in signals or "process_query_failed" in signals:
        code, action = "PROCESS_QUERY_FAILED", "Only the process query is known to have failed. Use a scoped Get-Process query for the known PID/name; do not kill, replace or launch an unverified helper."
        scope = "read_only_process_inspection"
        if devtools_authorized and host_allows_devtools:
            code, status = "CHECK_AHK_WITHOUT_CIM", "check"
        checks = ["Use browser-helper inspect with the saved identity, or the exact chosen AutoHotkey executable; no unrelated process enumeration.",
                  "An inaccessible process is not absent. An existing helper without current-run script identity is unverified and must not be replaced.",
                  "Only a verified absence permits a separately authorized helper start; manual native-prompt confirmation remains the default."]
    elif devtools_authorized and host_allows_devtools:
        code, status, scope = "CHECK_AUTHORIZED_DEVTOOLS", "check", "bounded_native_connection_check"
        action = "Use only native Chrome DevTools on the existing Chrome profile, without any extension preflight. This is connection guidance, not collection permission or proof of login."
        checks = ["Discover list_pages/select_page/evaluate_script for the permitted native DevTools surface; do not substitute a read-only DOM evaluate.",
                  "Have the user confirm the native remote-debugging prompt. An AHK helper is optional and needs separate current approval and exact identity checks.",
                  "Make one bounded list_pages connection call, then stop only your verified helper if one was started.",
                  "Select the real CRM/profile page, verify host/path and visible login/risk state. Only then use the receiver's devtoolsFunctionFile in an allowed page-context call.",
                  "The collector's actual API checks and all six collection validators still apply; a successful connection never marks a phase complete."]
    else:
        code, action = "NATIVE_DEVTOOLS_REQUIRED", "This collection requires native Chrome DevTools. Verify current-task authorization and host permission before the bounded native connection check; do not initialize or troubleshoot an extension."
    return {"readOnly": True, "status": status, "code": code, "nextAction": action, "nextChecks": checks,
            "automaticRetry": False, "collectionAllowed": False, "diagnosticScope": scope,
            "requiredBrowserChannel": "native_chrome_devtools" if state.get("collection") else None,
            "authorizationObserved": {"nativeDevtools": devtools_authorized, "hostAllowsDevtools": host_allows_devtools, "ahkHelper": ahk_authorized},
            "siteState": site or "unknown", "observedSignals": list(signals), "runStatus": current}


def configuration(target=None, dictionary=None, sales_map=None, node=None):
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if target:
        require(date.fromisoformat(target).isoformat() == target, "target date must use YYYY-MM-DD")
    target = date.fromisoformat(target) if target else today - timedelta(days=1)
    require(target < today, "collection date must be a completed Beijing day")
    runtime = shutil.which(node or "node")
    require(runtime, "Node.js is required for list enrichment; pass --node if not on PATH")
    spec = {"targetDate": target.isoformat(), "endDate": (target+timedelta(days=1)).isoformat(), "node": runtime}
    for key, value in (("dictionary", dictionary), ("salesMap", sales_map)):
        spec[key] = {"path": str(Path(value).resolve()), "sha256": digest(value)} if value else None
    if sales_map:
        mapping = read_json(sales_map)
        timestamp(mapping.get("verifiedAt"))
        require(mapping.get("includesUnassigned") is True and isinstance(mapping.get("segments"), list) and mapping["segments"], "sales map must include verified unassigned coverage")
        values = [s.get("salesId") for s in mapping["segments"]]
        require(all(isinstance(v, str) for v in values) and len(values) == len(set(values)), "sales segments need unique exact salesId values")
    return spec


def folder(run):
    return Path(run)/"collection"


def paths(run, state):
    base = folder(run)
    stem = "nanyue_due_diligence_input_" + state["collection"]["targetDate"]
    return base/"queryCustomerList.daily.enriched.json", base/(stem+".json"), base/(stem+".xlsx")


def config_check(state):
    for key in ("dictionary", "salesMap"):
        spec = state["collection"].get(key)
        if spec:
            require(digest(spec["path"]) == spec["sha256"], "collection configuration changed; create a new run")


def wrapper_check(value, state, segment=None):
    config_check(state)
    require(isinstance(value, dict) and value.get("ok") is True and value.get("status") == 200, "list API did not succeed")
    timestamp(value.get("capturedAt"))
    payload, criteria = value.get("json", {}), value.get("criteria", {})
    data = payload.get("data", {})
    rows, total = data.get("data"), data.get("total")
    require(payload.get("success") is True and type(total) is int and total >= 0 and isinstance(rows, list), "invalid list response; not zero customers")
    require(len(rows) == total == criteria.get("total") == criteria.get("recordsCollected"), "list count mismatch")
    indexed = index_by_customer_id(rows, "list")
    require(len(indexed) == total, "list identity coverage mismatch")
    spec = state["collection"]
    require(criteria.get("startDate") == spec["targetDate"] and criteria.get("endDate") == spec["endDate"], "list date mismatch")
    request = value.get("requestBody") or {}
    require(extract_source_date({"source": {"requestBody": request}}) == spec["targetDate"], "list filter date mismatch")
    filters = json.loads(request["jsonArray"])
    require(len([f for f in filters if f.get("id") == "667"]) == 1, "missing exact date filter")
    require(all(f.get("id") in {"667", "669"} for f in filters), "unexpected list filter")
    sales = [f for f in filters if f.get("id") == "669"]
    if segment is None:
        require(not sales and criteria.get("salesId") is None and criteria.get("salesName") == "全部", "list must cover all sales")
    else:
        require(len(sales) == 1 and sales[0].get("sales_id") == segment and criteria.get("salesId") == segment and total <= 5000, "sales segment scope mismatch")
    require(criteria.get("pageSize") == 500, "expected pageSize 500 with explicit-size-rejection fallback")
    if total > 5000:
        require(spec.get("salesMap"), "over 5000 requires a verified account sales map")
        expected = [s["salesId"] for s in read_json(spec["salesMap"]["path"])["segments"]]
        segments = value.get("segments", [])
        require([s.get("criteria", {}).get("salesId") for s in segments] == expected, "missing or reordered sales segments")
        merged = {}
        for part, sales_id in zip(segments, expected):
            for cid, row in wrapper_check(part, state, sales_id).items():
                require(cid not in merged or merged[cid] == row, "inconsistent duplicate across sales segments")
                merged[cid] = row
        require(merged == indexed, "split collection does not cover the global list")
    safety_walk(value, private=True)
    return indexed


def profile_url(value):
    require(isinstance(value, str), "profile locator must be text")
    text = value.strip()
    text = "https:" + text if text.startswith("//") else "https://" + text if text.startswith("profile.alibaba.com/") else text
    parts = urlsplit(text)
    require(parts.scheme in {"http", "https"} and parts.hostname == "profile.alibaba.com" and
            parts.path.lower() in {"/profile/myprofile.htm", "/profile/my_profile.htm"} and
            not parts.username and not parts.password and not parts.port, "invalid profile locator; inspect privately")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))


def profile_queue(details):
    result = []
    for row in details:
        contacts = row.get("fieldsMissingFromList", {}).get("contacts") or row.get("contacts") or []
        link = next((c.get("buyerProfileLink") for c in contacts if c.get("buyerProfileLink")), None)
        if link:
            profile_url(link)  # Fail malformed nonempty links; never silently discard one.
            result.append({"customerId": row["customerId"], "companyName": row.get("companyName"), "buyerProfileLink": link})
    return result


def progress_check(base, kind, expected, run_id):
    data_name, progress_name, errors_name = filenames(kind)
    rows = read_jsonl(base/data_name)
    index = index_by_customer_id(rows, kind)
    require(set(index) == set(expected) and not any(r.get("error") for r in rows), kind+": missing/extra/failed rows")
    progress = read_json(base/progress_name)
    require(progress.get("runId") == run_id and progress.get("kind") == kind, "progress belongs to another run")
    for key, value in {"total": len(expected), "doneCount": len(expected), "failedCount": 0, "pendingCount": 0}.items():
        require(progress.get(key) == value, kind+": progress is not complete")
    errors = read_jsonl(base/errors_name)
    require(all(r.get("customerId") in index for r in errors), kind+": unresolved error rows")
    safety_walk(rows, private=True)
    return rows


def filenames(kind):
    return ("detail_missing_fields.jsonl", "progress.json", "errors.jsonl") if kind == "detail" else ("buyer_profile_fields.jsonl", "buyer_profile_progress.json", "buyer_profile_errors.jsonl")


def validate(run, state, index):
    base = folder(run)
    listing, source, xlsx = paths(run, state)
    wrapper = read_json(base/"list.wrapper.json")
    expected = wrapper_check(wrapper, state)
    enriched = read_json(listing)
    actual, _, _ = list_identity_map(enriched["records"])
    require(set(actual) == set(expected) and list(actual) == list(expected), "enriched list identity/order mismatch")
    require(extract_source_date(enriched) == state["collection"]["targetDate"], "enriched date mismatch")
    receipt = read_json(base/"preparation.json")
    require(receipt == {"wrapperSha256": digest(base/"list.wrapper.json"), "enrichedSha256": digest(listing), "runId": state["runId"]}, "collection preparation changed")
    names = ["list.wrapper.json", listing.name, "preparation.json"]
    summary = {"sourceDate": state["collection"]["targetDate"], "uniqueCustomers": len(actual)}
    if index >= 1:
        require(actual, "zero customers must use the explicit no-customers receipt")
        details = progress_check(base, "detail", actual, state["runId"])
        names.extend(filenames("detail"))
        summary["detailRows"] = len(details)
    if index >= 2:
        queue = profile_queue(details)
        summary.update(profileLinkCount=len(queue), excludedNoProfileLinkCount=len(details)-len(queue))
    if index >= 3:
        if queue:
            profiles = progress_check(base, "profile", [r["customerId"] for r in queue], state["runId"])
            links = {r["customerId"]: r["buyerProfileLink"] for r in queue}
            require(all(p.get("buyerProfileLink") == p.get("profileUrl") == links[p["customerId"]] for p in profiles), "private profile locator changed")
            names.extend(filenames("profile"))
        else:
            require(not (base/"buyer_profile_fields.jsonl").exists() or not read_jsonl(base/"buyer_profile_fields.jsonl"), "stale profiles in no-link run")
        summary["buyerProfileStatus"] = "complete" if queue else "skipped_no_profile_links"
    if index >= 4:
        wanted = build_payload(listing, base/"detail_missing_fields.jsonl", base/"buyer_profile_fields.jsonl" if queue else None, 10)
        payload = read_json(source)
        require(all(payload.get(k) == v for k, v in wanted.items() if k != "generatedAt"), "v3 source differs from same-run artifacts")
        validate_pair(source, xlsx)
        names.extend((source.name, xlsx.name))
        summary.update(nanyueRecords=len(wanted["records"]), nanyueJson=str(source), nanyueWorkbook=str(xlsx))
    if index == 5:
        checkpoint = read_json(base/"phase_status.json")
        require(all(checkpoint.get("phases", {}).get(p, {}).get("status") == "complete" for p in PHASES[:-1]), "collection phase missing")
    return {"files": {n: digest(base/n) for n in names}, "summary": summary}


def prepare(run, state):
    base = folder(run)
    listing, _, _ = paths(run, state)
    wrapper_check(read_json(base/"list.wrapper.json"), state)
    if listing.exists():
        return  # Never overwrite a previously prepared list.
    config = state["collection"]
    script = "const e=require(process.argv[1]);process.stdout.write(JSON.stringify(e.buildEnrichedList(process.argv[2],process.argv[3]||null)))"
    result = subprocess.run([config["node"], "-e", script, str(ROOT/"enrich.js"), str(base/"list.wrapper.json"), (config.get("dictionary") or {}).get("path", "")],
                            capture_output=True, encoding="utf-8", timeout=60)
    require(result.returncode == 0, "list enrichment failed; inspect local input without logging private values")
    enriched = sanitize_private_source(json.loads(result.stdout))
    list_identity_map(enriched["records"])
    atomic(listing, enriched)
    atomic(base/"preparation.json", {"wrapperSha256": digest(base/"list.wrapper.json"), "enrichedSha256": digest(listing), "runId": state["runId"]})


def advance(run, state, index):
    base = folder(run)
    listing, source, xlsx = paths(run, state)
    if index == 0:
        prepare(run, state)
    if index == 4 and not source.exists():
        details = read_jsonl(base/"detail_missing_fields.jsonl")
        payload = build_payload(listing, base/"detail_missing_fields.jsonl", base/"buyer_profile_fields.jsonl" if profile_queue(details) else None, 10)
        atomic(source, payload)
    if index == 4 and not xlsx.exists():
        write_workbook(xlsx, read_json(source))
    proof = validate(run, state, index)
    checkpoint_path = base/"phase_status.json"
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else {"phases": {}}
    phase = PHASES[index]
    checkpoint["phases"][phase] = {"status": "complete", "errors": [], "summary": proof["summary"], "checkedAt": now()}
    checkpoint.update(currentPhase=phase, lastCheckedPhase=phase, lastStatus="complete")
    atomic(checkpoint_path, checkpoint)
    if index == 5:
        state["inputs"] = {k: {"path": str(p), "sha256": digest(p)} for k, p in (("json", source), ("xlsx", xlsx), ("phase", checkpoint_path))}
    return proof
