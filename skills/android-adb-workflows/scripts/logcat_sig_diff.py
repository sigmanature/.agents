#!/usr/bin/env python3

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


_RE_AM_CRASH = re.compile(r"\bam_crash:\s*\[(?P<body>.*)\]\s*$")
_RE_REDACT_DATA_APP = re.compile(r"/data/app/~~[^/]+==/[^/]+")


@dataclass(frozen=True)
class CrashSig:
    process: str
    exception: str
    message: str

    def normalized(self) -> "CrashSig":
        msg = _RE_REDACT_DATA_APP.sub("/data/app/<redacted>", self.message)
        # Keep noise down for common-vs-unique comparisons.
        msg = re.sub(r"\s+", " ", msg).strip()
        return CrashSig(self.process, self.exception, msg)

    def key(self) -> str:
        n = self.normalized()
        return f"{n.process}|{n.exception}|{n.message}"


def _iter_lines(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")


def _parse_am_crash(line: str) -> Optional[CrashSig]:
    """
    Parse Android events-buffer style crash line:
      I am_crash: [pid,uid,process,crashId,exception,message,sourceFile,sourceLine,0]

    Note: message can contain commas, so we parse from the end first.
    """
    m = _RE_AM_CRASH.search(line)
    if not m:
        return None

    body = m.group("body")
    try:
        head, source_file, source_line, _tail = body.rsplit(",", 3)
        pid, uid, process, crash_id, exception, message = head.split(",", 5)
    except ValueError:
        return None

    process = process.strip()
    exception = exception.strip()
    message = message.strip()
    if source_file.strip():
        # Keep the (file:line) in message for extra grouping signal, but do not let it dominate.
        message = f"{message} @ {source_file.strip()}:{source_line.strip()}"
    return CrashSig(process=process, exception=exception, message=message)


def _extract_fs_signals(line: str) -> Optional[str]:
    # Focused set for large-folio / FS breakage triage.
    needles = (
        "Operation not supported on transport endpoint",
        "SQLiteDiskIOException",
        "disk I/O error",
        "Failed to open APK",
        ": I/O error",
    )
    if any(n in line for n in needles):
        return _RE_REDACT_DATA_APP.sub("/data/app/<redacted>", line)
    return None


def _collect(path: Path) -> tuple[Counter[str], Counter[str]]:
    crash_sigs: Counter[str] = Counter()
    fs_lines: Counter[str] = Counter()

    for line in _iter_lines(path):
        sig = _parse_am_crash(line)
        if sig is not None:
            crash_sigs[sig.key()] += 1

        fs = _extract_fs_signals(line)
        if fs is not None:
            fs_lines[fs] += 1

    return crash_sigs, fs_lines


def _print_top(title: str, c: Counter[str], limit: int) -> None:
    print(f"\n== {title} (top {limit}) ==")
    if not c:
        print("(none)")
        return
    for k, v in c.most_common(limit):
        print(f"{v:6d}  {k}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two offline logcat_all.txt files (common vs unique crash signatures).")
    ap.add_argument("--a", required=True, type=Path, help="Device A logcat_all.txt")
    ap.add_argument("--b", required=True, type=Path, help="Device B logcat_all.txt")
    ap.add_argument("--top", type=int, default=30, help="Top-N lines per section")
    args = ap.parse_args()

    a_crash, a_fs = _collect(args.a)
    b_crash, b_fs = _collect(args.b)

    print(f"A: {args.a}  am_crash sigs={len(a_crash)}  fs-signal lines={len(a_fs)}")
    print(f"B: {args.b}  am_crash sigs={len(b_crash)}  fs-signal lines={len(b_fs)}")

    a_only_crash = Counter({k: v for k, v in a_crash.items() if k not in b_crash})
    b_only_crash = Counter({k: v for k, v in b_crash.items() if k not in a_crash})
    common_crash = Counter({k: min(a_crash[k], b_crash[k]) for k in (a_crash.keys() & b_crash.keys())})

    a_only_fs = Counter({k: v for k, v in a_fs.items() if k not in b_fs})
    b_only_fs = Counter({k: v for k, v in b_fs.items() if k not in a_fs})
    common_fs = Counter({k: min(a_fs[k], b_fs[k]) for k in (a_fs.keys() & b_fs.keys())})

    _print_top("A-only am_crash signatures", a_only_crash, args.top)
    _print_top("B-only am_crash signatures", b_only_crash, args.top)
    _print_top("Common am_crash signatures", common_crash, args.top)

    _print_top("A-only FS-ish lines", a_only_fs, args.top)
    _print_top("B-only FS-ish lines", b_only_fs, args.top)
    _print_top("Common FS-ish lines", common_fs, args.top)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

