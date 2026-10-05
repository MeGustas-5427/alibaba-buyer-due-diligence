"""Offline regressions: bad bytes, CSV limits, streaming, memory cap, cleanup."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import screen_uk_sanctions as target

passed = []


def check(name, callback):
    callback()
    passed.append(name)


def rejects(callback, fragment):
    try:
        callback()
    except (ValueError, UnicodeError, csv.Error, RuntimeError) as error:
        assert fragment.lower() in str(error).lower(), (fragment, str(error))
    else:
        raise AssertionError("Expected rejection: " + fragment)


def main():
    with tempfile.TemporaryDirectory(prefix="uk-screening-test-") as temp:
        base = Path(temp)
        csv_path = base / "fixture.csv"
        fields = ["Unique ID", *target.NAME_FIELDS, "Name non-latin script", "Name type"]
        def make(rows=2, preamble=True):
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                if preamble:
                    f.write("Report Date: 23-Sep-2026\n")
                writer = csv.writer(f)
                writer.writerow(fields)
                for i in range(rows):
                    writer.writerow([f"T{i}", "ALICE" if i == 0 else "BOB", "", "", "", "", "SMITH",
                                     "爱丽丝" if i == 0 else "", "Primary Name"])
        terms = {target.normalize(t): t for t in [" Alice   Smith ", "爱丽丝", "Definitely Absent"]}
        make()
        result = target.scan(csv_path, terms)
        assert result["rows"] == 2 and result["hitCount"] == 2 and result["eofReached"]
        passed.append("positive-negative-unicode-BOM-and-report-date-controls")
        make(preamble=False)
        assert target.scan(csv_path, terms)["hitCount"] == 2
        passed.append("header-without-preamble")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            writer.writerows([
                ["G1", "", "", "", "", "", "", "", ""],
                ["G1", "ALICE", "", "", "", "", "SMITH", "", "Primary Name"],
                ["G1", "ALICE", "", "", "", "", "SMITH", "", "Primary Name"],
                ["G2", "BOB", "", "", "", "", "SMITH", "", "Primary Name"],
            ])
        with patch.object(target, "MAX_HITS", 1):
            result = target.scan(csv_path, terms)
        assert result["rows"] == 4 and result["hitCount"] == 1
        assert result["hits"] == [{"term": " Alice   Smith ", "uniqueId": "G1", "row": 2}]
        passed.append("identity-level-name-validation-and-permutation-deduplication")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            writer.writerow(["G1", "", "", "", "", "", "", "", ""])
        check("identity-without-any-name-rejected",
              lambda: rejects(lambda: target.scan(csv_path, terms), "identity has no name"))
        make(rows=3)
        with patch.object(target, "MAX_IDENTITIES", 2):
            check("unique-identity-cap",
                  lambda: rejects(lambda: target.scan(csv_path, terms), "unique identities"))
        csv_path.write_bytes(b"78\n97\n109\n101\n")
        check("byte-enumeration-is-not-CSV", lambda: rejects(lambda: target.scan(csv_path, terms), "header"))
        csv_path.write_bytes(b"78 97 109 101")
        check("string-cast-numbers-rejected", lambda: rejects(lambda: target.scan(csv_path, terms), "header"))
        csv_path.write_bytes(b"\xff\xfeBAD")
        check("invalid-encoding-rejected", lambda: rejects(lambda: target.scan(csv_path, terms), "utf-8"))
        csv_path.write_text("<html>Access denied</html>", encoding="utf-8")
        check("HTML-error-page-rejected", lambda: rejects(lambda: target.scan(csv_path, terms), "header"))
        make(rows=0)
        check("header-only-not-zero-hits", lambda: rejects(lambda: target.scan(csv_path, terms), "small"))
        make(rows=1)
        with csv_path.open("a", encoding="utf-8") as f:
            f.write("broken,row\n")
        check("malformed-tail-invalidates-earlier-results", lambda: rejects(lambda: target.scan(csv_path, terms), "column"))
        make(rows=0)
        with csv_path.open("a", encoding="utf-8") as f:
            f.write("X," + "A" * (target.MAX_FIELD + 1) + "\n")
        check("oversized-field-rejected", lambda: rejects(lambda: target.scan(csv_path, terms), "field"))
        make(rows=0)
        with csv_path.open("a", encoding="utf-8") as f:
            f.write('X,"' + ("\n" * 4097))
        with patch.object(target, "MAX_RECORD", 4096):
            check("multiline-record-budget", lambda: rejects(lambda: target.scan(csv_path, terms), "record"))
        make(rows=0)
        with csv_path.open("a", encoding="utf-8") as f:
            f.write('X,"unclosed')
        check("truncated-quoted-record-rejected", lambda: rejects(lambda: target.scan(csv_path, terms), "end of data"))
        make(rows=target.MAX_HITS + 1)
        hit_terms = {"alice smith": "Alice Smith", "bob smith": "Bob Smith"}
        check("hit-buffer-cap-not-partial-success", lambda: rejects(lambda: target.scan(csv_path, hit_terms), "hit buffer"))
        make(rows=3)
        with patch.object(target, "MAX_ROWS", 2):
            check("row-limit", lambda: rejects(lambda: target.scan(csv_path, terms), "rows"))
        make(rows=1)
        with patch.object(target, "MAX_BYTES", 5):
            check("input-byte-cap", lambda: rejects(lambda: target.scan(csv_path, terms), "64 MiB"))
        terms_path = base / "terms.json"
        terms_path.write_text(json.dumps(["Alice Smith"]), encoding="utf-8")
        assert target.terms_from_file(terms_path)[0] == {"alice smith": "Alice Smith"}
        terms_path.write_text('["' + "x" * 65536 + '"]', encoding="utf-8")
        check("terms-byte-cap", lambda: rejects(lambda: target.terms_from_file(terms_path), "64 KiB"))

        class Response(io.BytesIO):
            status = 200
            def __init__(self, content, length=None):
                super().__init__(content)
                from email.message import Message
                self.headers = Message()
                self.headers["Content-Type"] = "application/octet-stream"
                if length is not None:
                    self.headers["Content-Length"] = str(length)
            def geturl(self):
                return target.URL
        for name, body, length, cap, fragment in [
            ("declared-size-cap", b"x", target.MAX_BYTES + 1, target.MAX_BYTES, "Content-Length"),
            ("unknown-length-actual-cap", b"x" * 100, None, 50, "actual download"),
            ("truncated-download", b"x", 100, target.MAX_BYTES, "truncated"),
        ]:
            with patch.object(target.urllib.request, "urlopen", return_value=Response(body, length)), \
                    patch.object(target, "MAX_BYTES", cap):
                check(name, lambda: rejects(lambda: target.download(base / (name + ".csv")), fragment))

        make(rows=30000)
        tracemalloc.start()
        result = target.scan(csv_path, {"absent": "Absent"})
        _, python_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert result["rows"] == 30000 and python_peak < 16 * target.MIB, python_peak
        passed.append("30000-row-stream-memory-below-16-MiB")

        # Windows tests use a 64 MiB job; never allocate system-scale memory.
        prefix = "import sys; assert sys.stdin.buffer.read(1)==b'G'; "
        code, out, _, peak = target.guarded_run(
            [target.worker_python(), "-c", prefix + "print('guarded')"], timeout=10, memory=64 * target.MIB)
        assert code == 0 and out.strip() == b"guarded" and peak < 64 * target.MIB
        passed.append("Windows-job-attached-before-work")
        probe = prefix + "\ntry:\n x=bytearray(128*1024*1024)\n print('UNBOUNDED')\nexcept MemoryError:\n print('LIMIT_ENFORCED')"
        code, out, _, memory_peak = target.guarded_run(
            [target.worker_python(), "-c", probe], timeout=10, memory=64 * target.MIB)
        # The job peak counter can include a refused allocation on this Windows build.
        # The allocation fails with MemoryError, and guarded_run reads back the set cap.
        assert code == 0 and b"LIMIT_ENFORCED" in out, (code, out, memory_peak)
        passed.append("Windows-commit-cap-rejects-128-MiB-allocation")
        process_id_path = base / "timeout.pid"
        timeout_probe = prefix + f"from pathlib import Path; import os,time; Path({str(process_id_path)!r}).write_text(str(os.getpid())); time.sleep(30)"
        try:
            target.guarded_run([target.worker_python(), "-c", timeout_probe], timeout=1, memory=64 * target.MIB)
        except subprocess.TimeoutExpired:
            assert process_id_path.exists()
            api, _ = target.job_api()
            handle = api.OpenProcess(0x1000, False, int(process_id_path.read_text()))
            if handle:
                try:
                    api.GetExitCodeProcess.argtypes = [target.w.HANDLE, target.ctypes.POINTER(target.w.DWORD)]
                    api.GetExitCodeProcess.restype = target.w.BOOL
                    exit_code = target.w.DWORD()
                    assert api.GetExitCodeProcess(handle, target.ctypes.byref(exit_code))
                    assert exit_code.value != 259, "Timed-out worker is still running"
                finally:
                    api.CloseHandle(handle)
        else:
            raise AssertionError("Expected worker timeout")
        passed.append("timeout-kills-and-reaps-worker")
        print(json.dumps({"passed": len(passed), "checks": passed,
                          "pythonParserPeakBytes": python_peak,
                          "limitedWorkerWin32PeakCounter": memory_peak}, indent=2))


if __name__ == "__main__":
    main()
