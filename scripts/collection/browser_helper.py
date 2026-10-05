"""Optional Windows helper lifecycle. No bundled clicker and no browser/tool fallback."""
import os
from contextlib import nullcontext
from pathlib import Path
import shutil
import subprocess

from extract_research_view import atomic, digest, now, read_json, require, safe_text
from . import collect


def operate(run, action, *, executable=None, script=None, approval=None, host_policy=None,
            devtools_authorized=False, host_allows_devtools=False, ahk_authorized=False, signals=()):
    import workflow
    run = Path(run).resolve()
    require(action in {"inspect", "start", "stop"}, "unknown helper action")
    state = workflow.load_state(run)
    require(state.get("collection"), "helper is only available for a collection run")
    require(os.name == "nt", "optional AHK helper requires Windows; use manual native-prompt confirmation elsewhere")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    require(shell, "PowerShell is required for scoped helper inspection")
    base = collect.folder(run)
    record = base/"browser-helper-identity.private.json"
    authority = base/"browser-helper-authorization.private.json"
    require(not record.is_symlink() and not authority.is_symlink(), "unsafe helper receipt path")
    args = [shell, "-NoProfile", "-NonInteractive", "-File", str(collect.ROOT/"browser_helper.ps1"),
            "-Action", action, "-RunPath", str(run), "-RunId", state["runId"], "-RecordPath", str(record)]
    with (workflow.locked(run/".browser-helper.lock") if action != "inspect" else nullcontext()):
        if action == "start":
            require(devtools_authorized and host_allows_devtools and ahk_authorized,
                    "helper start requires current native DevTools approval, host permission and separate AHK approval")
            require(isinstance(approval, str) and approval.strip() and isinstance(host_policy, str) and host_policy.strip(),
                    "record the exact current user approval and applicable host policy reference")
            safe_text(approval)
            safe_text(host_policy)
            guidance = collect.diagnose(run, signals, devtools_authorized=True, host_allows_devtools=True, ahk_authorized=True)
            require(guidance["status"] == "check" and guidance["code"] in {"CHECK_AUTHORIZED_DEVTOOLS", "CHECK_AHK_WITHOUT_CIM"},
                    "helper start blocked by run, site, validator or unresolved collection error")
            require(workflow.inspect(run, state)[0] < len(collect.PHASES), "collection already complete; do not launch a helper")
            require(executable and script, "supply the reviewed local AutoHotkey executable and script explicitly")
        if executable:
            executable = Path(executable).resolve(strict=True)
            require(executable.is_file() and executable.name.lower() in {"autohotkey64.exe", "autohotkey32.exe"}, "expected an explicitly selected AutoHotkey v2 executable")
            args.extend(("-Executable", str(executable)))
        if script:
            script = Path(script).resolve(strict=True)
            require(script.is_file() and script.suffix.lower() == ".ahk", "expected the reviewed local AHK script")
            args.extend(("-ScriptFile", str(script)))
        if action == "start":
            atomic(authority, {"schemaVersion": "alibaba.browser-helper-authorization.v1", "runId": state["runId"],
                              "run": str(run), "checkedAt": now(), "userApproval": approval, "hostPolicy": host_policy,
                              "nativeDevtools": True, "hostAllowsDevtools": True, "ahkHelper": True,
                              "executable": str(executable), "script": str(script),
                              "executableSha256": digest(executable), "scriptSha256": digest(script)})
            args.extend(("-AuthorityPath", str(authority)))
        result = subprocess.run(args, capture_output=True, encoding="utf-8-sig", timeout=20)
        # PowerShell may include private paths or arbitrary script output on failure; do not echo it.
        require(result.returncode == 0, "helper inspection/lifecycle failed; retain its identity and inspect locally, never infer absence")
        import json
        value = json.loads(result.stdout)
        require(value.get("runId") == state["runId"] and value.get("status") in {"absent", "exited", "verified", "started", "stopped", "unverified"},
                "invalid helper result; do not reuse or terminate a process")
        if action == "start" and value["status"] == "started":
            saved = read_json(record)
            require(saved["runId"] == state["runId"] and saved["pid"] == value["pid"] and saved["run"] == str(run), "helper launch receipt mismatch")
        return {**value, "collectionAllowed": False, "identityFile": str(record),
                "note": "Process identity only, not proof of a browser connection, login or API success."}
