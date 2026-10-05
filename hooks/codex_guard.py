"""Thin, synchronous Codex hooks. No active binding means no action.

Not a security sandbox: hooks may be untrusted, unavailable or fail open. All
authoritative checks live in workflow.py. Never spawn Codex from a hook.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from extract_research_view import Invalid, atomic, encode, read_json, require
from workflow import (event, inspect, load_state, locked, normalized,
                      reconcile, research_offset, save_state, stages, status)

EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop")


def binding(payload):
    if not payload.get("session_id") or not payload.get("cwd"):
        return None
    workspace = Path(payload["cwd"]).resolve()
    registry = workspace/".codex"/"alibaba-due-diligence.bindings.json"
    if not registry.is_file():
        return None
    candidates = []
    for item in read_json(registry):
        if item.get("sessionId") != payload["session_id"] or item.get("workspace") != normalized(workspace):
            continue
        state = load_state(item["runPath"])
        require(state.get("sessionId") == item["sessionId"] and state.get("workspace") == item["workspace"] and state.get("runId") == item["runId"], "hook/run binding mismatch")
        if state["status"] != "cancelled" and not status(item["runPath"], state)["complete"]:
            candidates.append(Path(item["runPath"]))
    require(len(candidates) <= 1, "multiple active runs bound to session; resolve explicitly, not by recency")
    return candidates[0] if candidates else None


def premature(payload, run, next_index):
    """Only deny positively identified actions on this exact run, not prose."""
    name = str(payload.get("tool_name", ""))
    value = payload.get("tool_input", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {"input": value}
    if not isinstance(value, dict):
        return None
    text = str(value.get("command") or value.get("cmd") or value.get("patch") or value.get("input") or "")
    text = text.replace("\\", "/").lower()
    bound = run.as_posix().lower() in text or run.name.lower() in text
    if not bound:
        return None
    if name in {"apply_patch", "Edit", "Write", "functions.apply_patch"} or name.endswith("__apply_patch"):
        if re.search(r"(?:add|update) file:.*(?:validation\.json|workflow_state\.json)", text) and next_index < 6:
            return "Do not forge completion receipts/state; use workflow.py advance after producing evidence."
    if name in {"Bash", "exec_command", "functions.exec_command"} or name.endswith("__exec_command"):
        if "generate_initial_screening_pdf.py" in text and next_index < 5:
            return "PDF generation requires validated analysis."
        if re.search(r"workflow\.py[\"']?\s+finalize\b", text) and next_index < 6:
            return "Finalization requires every earlier stage."
    return None


def context(event_name, message):
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": message}}


def handle(payload):
    event_name = payload.get("hook_event_name")
    if event_name not in EVENTS:
        return {}
    run = binding(payload)
    if run is None:
        return {}
    with locked(run/".lock"):
        state = load_state(run)
        seen = state.setdefault("hookEventsObserved", [])
        if event_name not in seen:
            seen.append(event_name)
        index, reason = reconcile(run, state)
        sequence = stages(state)
        event(run, "hook_"+event_name, sequence[index] if index < len(sequence) else None)
        active = state["status"] == "active" and index < len(sequence)
        message = f"Alibaba due diligence run {run.name}: {sequence[index] if index < len(sequence) else 'complete'}. {reason} Run workflow.py next --run \"{run}\"."
        result = {}
        if event_name == "SessionStart":
            result = context(event_name, message)
        elif event_name == "PreToolUse" and active:
            denial = premature(payload, run, index-research_offset(state))
            if denial:
                result = {"hookSpecificOutput": {"hookEventName": event_name, "permissionDecision": "deny", "permissionDecisionReason": denial+" "+message}}
        elif event_name == "PostToolUse" and active:
            # Revalidation above invalidates modified evidence; tool success never passes a stage.
            result = context(event_name, message)
        elif event_name == "Stop" and active:
            signature = hashlib.sha256(encode({"index": index, "reason": reason, "proofs": state["proofs"]}).encode()).hexdigest()
            guard = state.get("stopGuard", {})
            attempts = guard.get("attempts", 0) if guard.get("fingerprint") == signature else 0
            state["stopGuard"] = {"fingerprint": signature, "attempts": attempts+1}
            if attempts < 2:
                result = {"decision": "block", "reason": message+" If an external prerequisite is unavailable, record a truthful blocked reason. Honor any user request to pause or stop."}
            else:
                result = {"systemMessage": "Alibaba workflow remains incomplete; automatic continuation stopped after two unchanged attempts. "+message}
        save_state(run, state)
        return result


def install(workspace, python):
    """Register project-local commands without changing other hooks or trust."""
    workspace = Path(workspace).resolve()
    require(workspace.is_dir() and Path(python).is_file(), "workspace/Python must exist")
    config = workspace/".codex"/"hooks.json"
    # Native Windows hook runner defaults to COMSPEC /C, not the interactive shell.
    paths = [str(Path(python).resolve()), str(Path(__file__).resolve())]
    require(not any(re.search(r'[\r\n%!"]', p) for p in paths), "hook path has unsupported shell metacharacters")
    command = f'"{paths[0]}" -X utf8 "{paths[1]}"' if os.name == "nt" else shlex.join([paths[0], "-X", "utf8", paths[1]])
    with locked(config.with_suffix(".lock")):
        current = read_json(config) if config.exists() else {"hooks": {}}
        require(isinstance(current.get("hooks"), dict), "unexpected hook config shape; merge manually")
        original = encode(current)
        for name in EVENTS:
            groups = current["hooks"].setdefault(name, [])
            if not any(h.get("command") == command for group in groups for h in group.get("hooks", [])):
                group = {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
                if name in {"PreToolUse", "PostToolUse"}:
                    group["matcher"] = "Bash|exec_command|functions\\.exec_command|apply_patch|Edit|Write|functions\\.apply_patch"
                groups.append(group)
        if config.exists() and not config.with_suffix(".json.before-alibaba").exists():
            atomic(config.with_suffix(".json.before-alibaba"), original)
        atomic(config, current)
    return {"registered": str(config), "trusted": "not_checked", "liveProbes": "not_run", "next": "Review the exact project hooks using Codex native /hooks UI and grant trust if appropriate. Restart/resume a session, then run controlled shell/patch/code-mode probes; config presence is not activation evidence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    try:
        if args.install:
            result = install(args.install, args.python)
        else:
            raw = sys.stdin.buffer.read(1024*1024+1)
            require(len(raw) <= 1024*1024, "hook input too large")
            result = handle(json.loads(raw.decode("utf-8-sig")))
        print(encode(result))
        return 0
    except Exception as exc:
        # Malformed/untrusted hooks can fail open in the host; don't claim an enforcement guarantee.
        message = str(exc) if isinstance(exc, Invalid) else type(exc).__name__
        print(encode({"systemMessage": "Alibaba workflow hook could not validate: "+message+". Python workflow completion checks remain required."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
