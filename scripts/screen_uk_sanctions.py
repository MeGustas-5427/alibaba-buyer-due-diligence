#!/usr/bin/env python
"""Bounded UK CSV screening. Stdlib only; the CLI requires Windows Job Objects."""
from __future__ import annotations

import argparse
import csv
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request

URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
MIB = 1024 * 1024
MAX_BYTES = 64 * MIB
MAX_RECORD = MIB
MAX_FIELD = 128 * 1024
MAX_ROWS = 500_000
MAX_IDENTITIES = 100_000
MAX_HITS = 256
MEMORY_BYTES = 256 * MIB
WALL_SECONDS = 120
NAME_FIELDS = [f"Name {i}" for i in range(1, 7)]
REQUIRED = ["Unique ID", *NAME_FIELDS, "Name non-latin script", "Name type"]


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def terms_from_file(path):
    with Path(path).open("rb") as f:
        data = f.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise ValueError("terms file exceeds 64 KiB")
    terms = json.loads(data.decode("utf-8-sig", errors="strict"))
    if (not isinstance(terms, list) or not 1 <= len(terms) <= 128
            or any(not isinstance(t, str) or not t.strip() or len(t) > 256 for t in terms)):
        raise ValueError("terms must be 1..128 nonempty strings, each at most 256 characters")
    return {normalize(t): t for t in terms}, hashlib.sha256(data).hexdigest()


def scan(path, terms, min_rows=1):
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError("CSV exceeds 64 MiB")
    csv.field_size_limit(MAX_FIELD)
    hits, hit_keys, identities, rows = [], set(), {}, 0
    with Path(path).open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
        # A closure lets csv.reader request several physical lines per record.
        record_chars = 0
        def lines():
            nonlocal record_chars
            while True:
                line = f.readline(MAX_RECORD + 1)
                if not line:
                    break
                record_chars += len(line)
                if record_chars > MAX_RECORD:
                    raise ValueError("CSV logical record exceeds 1 MiB of characters")
                if "\0" in line:
                    raise ValueError("NUL byte in CSV")
                yield line
        reader = csv.reader(lines(), strict=True)
        header = next(reader, None)
        record_chars = 0
        if header and len(header) == 1 and header[0].startswith("Report Date: "):
            header = next(reader, None)
            record_chars = 0
        if not header or len(header) != len(set(header)) or not set(REQUIRED) <= set(header):
            raise ValueError("UK CSV header missing, duplicated, or unexpected")
        if len(header) > 128:
            raise ValueError("CSV has too many columns")
        indexes = {name: header.index(name) for name in REQUIRED}
        for row in reader:
            record_chars = 0
            rows += 1
            if rows > MAX_ROWS:
                raise ValueError("CSV exceeds 500000 rows")
            if len(row) != len(header):
                raise ValueError(f"CSV column mismatch at data row {rows}")
            uid = row[indexes["Unique ID"]].strip()
            if not uid or len(uid) > 128:
                raise ValueError(f"missing or oversized Unique ID at data row {rows}")
            name = " ".join(row[indexes[k]].strip() for k in NAME_FIELDS).strip()
            local_name = row[indexes["Name non-latin script"]].strip()
            has_name = bool(name or local_name)
            if uid not in identities:
                if len(identities) >= MAX_IDENTITIES:
                    raise ValueError(f"CSV exceeds {MAX_IDENTITIES} unique identities")
                identities[uid] = has_name
            elif has_name:
                identities[uid] = True
            for candidate in (name, local_name):
                key = normalize(candidate)
                hit_key = (uid, key)
                if key in terms and hit_key not in hit_keys:
                    if len(hits) >= MAX_HITS:
                        raise ValueError("hit buffer limit exceeded; screening incomplete")
                    hit_keys.add(hit_key)
                    hits.append({"term": terms[key], "uniqueId": uid, "row": rows})
        if rows < min_rows:
            raise ValueError("empty or unexpectedly small UK list")
        missing_name = next((uid for uid, has_name in identities.items() if not has_name), None)
        if missing_name is not None:
            raise ValueError(f"identity has no name: Unique ID {missing_name}")
    return {"rows": rows, "hitCount": len(hits), "hits": hits, "eofReached": True}


def download(path):
    request = urllib.request.Request(URL, headers={"Accept-Encoding": "identity"})
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200 or response.geturl() != URL:
            raise ValueError("unexpected HTTP status or redirect")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("compressed response is not supported")
        media = response.headers.get_content_type()
        if media not in {"text/csv", "application/csv", "application/octet-stream", "text/plain"}:
            raise ValueError("unexpected content type")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
            raise ValueError("invalid or oversized Content-Length")
        with Path(path).open("xb") as dest:
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_BYTES:
                    raise ValueError("actual download exceeds 64 MiB")
                digest.update(block)
                dest.write(block)
        if not size or (length is not None and size != int(length)):
            raise ValueError(f"empty or truncated download: received={size}, declared={length}")
    return {"url": URL, "bytes": size, "sha256": digest.hexdigest(),
            "contentType": media, "encoding": "utf-8-sig/strict"}


def job_api():
    if os.name != "nt":
        raise RuntimeError("Windows Job Object guard required; refusing unguarded execution")
    class Basic(ctypes.Structure):
        _fields_ = [("user", ctypes.c_int64), ("job", ctypes.c_int64), ("flags", w.DWORD),
                    ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (args, ret) in declarations.items():
        fn = getattr(api, name)
        fn.argtypes, fn.restype = args, ret
    return api, Extended


def worker_python():
    # A Windows venv python.exe is a redirector: its child would escape or trip
    # the one-process Job Object. The worker is stdlib-only; use the real base
    # interpreter directly, retaining every resource cap and the G handshake.
    if os.name != "nt":
        raise RuntimeError("Windows Job Object guard required")
    executable = Path(sys.base_prefix) / "python.exe"
    if not executable.is_file():
        raise RuntimeError("base Python interpreter not found; refusing unguarded fallback")
    return str(executable)


def guarded_run(command, timeout=WALL_SECONDS, memory=MEMORY_BYTES):
    """The worker blocks on stdin until it is assigned to the limited job."""
    api, Extended = job_api()
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = None
    try:
        limits = Extended()
        # Kill-on-close + aggregate committed memory + only one worker process.
        limits.basic.flags = 0x2000 | 0x0200 | 0x0008
        limits.basic.active = 1
        limits.job_memory = memory
        if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        handle = api.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not api.AssignProcessToJobObject(job, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            api.CloseHandle(handle)
        # Worker emits one bounded JSON result; source bytes never reach stdout.
        stdout, stderr = process.communicate(b"G", timeout=timeout)
        measured = Extended()
        if not api.QueryInformationJobObject(job, 9, ctypes.byref(measured), ctypes.sizeof(measured), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if measured.job_memory != memory or measured.basic.flags & limits.basic.flags != limits.basic.flags:
            raise RuntimeError("job limit readback mismatch")
        return process.returncode, stdout, stderr, measured.peak_job
    finally:
        try:
            if process is not None and process.poll() is None:
                api.TerminateJobObject(job, 124)
                # Assignment can fail; still reap the exact process started here.
                process.kill()
                process.wait(timeout=5)
                process.communicate(timeout=5)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
        finally:
            api.CloseHandle(job)


def worker(args):
    if sys.stdin.buffer.read(1) != b"G":
        raise RuntimeError("supervisor handshake missing")
    terms, terms_hash = terms_from_file(args.terms_file)
    path = Path(args.worker) / "uk.csv"
    source = download(path)
    result = scan(path, terms, min_rows=1000)
    result.update({"schemaVersion": "alibaba.uk-screening.v1", "complete": True,
                   "source": source, "termsSha256": terms_hash,
                   "matchedMethod": "NFKC-casefold-whitespace-exact-name",
                   "termCount": len(terms),
                   "checkedAt": datetime.now(timezone.utc).isoformat()})
    print(json.dumps(result, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms-file", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            worker(args)
            return 0
        except Exception as error:
            print(json.dumps({"complete": False, "error": type(error).__name__ + ": " + str(error)[:300]}))
            return 2
    if args.out is None or args.out.exists():
        parser.error("--out must be a new file so stale evidence cannot be reused")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="alibaba-uk-screen-") as temp:
            command = [worker_python(), "-X", "utf8", str(Path(__file__).resolve()),
                       "--worker", temp, "--terms-file", str(args.terms_file.resolve())]
            code, stdout, stderr, peak = guarded_run(command)
            if code != 0:
                raise RuntimeError(f"worker exit={code}; {stdout[:400].decode('utf-8', 'replace')}")
            result = json.loads(stdout.decode("utf-8", "strict"))
            if result.get("complete") is not True or result.get("eofReached") is not True:
                raise ValueError("worker did not finish complete screening")
            result["guard"] = {"memoryLimitBytes": MEMORY_BYTES, "win32PeakJobMemoryUsed": peak,
                               "limitReadbackVerified": True,
                               "wallLimitSeconds": WALL_SECONDS, "workerExitCode": code,
                               "elapsedSeconds": round(time.monotonic() - started, 3)}
            with args.out.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(json.dumps({"complete": True, "out": str(args.out.resolve()),
                              "rows": result["rows"], "hitCount": result["hitCount"],
                              "win32PeakJobMemoryUsed": peak}))
            return 0
    except Exception as error:
        print(json.dumps({"complete": False, "error": type(error).__name__ + ": " + str(error)[:500]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
