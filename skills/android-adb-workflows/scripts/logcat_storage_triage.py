#!/usr/bin/env python3

"""
Offline logcat triage helper focused on "storage-ish" signals.

Why:
- When debugging large-folio / compression / mmap / filesystem regressions, the *first clue*
  is usually visible in user-space logs (SIGBUS, I/O error, Zip/dex open failures, SQLite
  corruption) or kernel/system logs (f2fs/ext4 errors).

This script is intentionally heuristic: it helps you quickly *see* whether a suspected
storage regression is actually showing up in logs, before you chase app-level stacktraces.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


_RE_AM_CRASH = re.compile(r"\bam_crash:\s*\[(?P<body>.*)\]\s*$")
_RE_REDACT_DATA_APP = re.compile(r"/data/app/~~[^/]+==/[^/]+")


_STORAGE_NEEDLES = (
    # Direct I/O / filesystem signals
    "Input/output error",
    "I/O error",
    "Read-only file system",
    "No space left on device",
    "disk I/O error",
    "SQLiteDiskIOException",
    "sqlite_db_corrupt:",
    # APK / dex / zip open problems
    "ZipException",
    "ziparchive",
    "ZipArchive",
    "Failed to open APK",
    "Failed to open dex",
    "Failed to open oat",
    # NOTE: avoid matching perfmgr "/proc/.../dex2oat/..." noise; keep dex signals scoped to failures above.
    # Common errno surfaces (keep scoped by "/data" later)
    "open failed:",
    "Permission denied",
    "No such file or directory",
    # Android storage stack
    "vold",
    "storaged",
    "fscrypt",
    # Kernel fs tags commonly bridged into logcat
    "f2fs",
    "F2FS",
    "ext4",
    "EXT4",
    # dm- device hints (userdata is typically dm-XX)
    "dev=\"dm-",
)


def _iter_lines(path: Path) -> Iterable[str]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")


@dataclass(frozen=True)
class CrashSig:
    process: str
    exception: str
    message: str

    def normalized(self) -> "CrashSig":
        msg = _RE_REDACT_DATA_APP.sub("/data/app/<redacted>", self.message)
        msg = re.sub(r"\s+", " ", msg).strip()
        return CrashSig(self.process, self.exception, msg)

    def key(self) -> str:
        n = self.normalized()
        return f"{n.process}|{n.exception}|{n.message}"


def _parse_am_crash(line: str) -> Optional[CrashSig]:
    """
    Parse Android events-buffer style crash line:
      I am_crash: [pid,uid,process,crashId,exception,message,sourceFile,sourceLine,0]

    Note: message can contain commas, so parse from the end first.
    """
    m = _RE_AM_CRASH.search(line)
    if not m:
        return None

    body = m.group("body")
    try:
        head, source_file, source_line, _tail = body.rsplit(",", 3)
        _pid, _uid, process, _crash_id, exception, message = head.split(",", 5)
    except ValueError:
        return None

    process = process.strip()
    exception = exception.strip()
    message = message.strip()
    if source_file.strip():
        message = f"{message} @ {source_file.strip()}:{source_line.strip()}"
    return CrashSig(process=process, exception=exception, message=message)


def _maybe_storage_line(line: str) -> Optional[str]:
    if not any(n in line for n in _STORAGE_NEEDLES):
        return None

    # Drop common non-storage noise: perf/thermal/scheduler writes into procfs/sysfs.
    if ("/proc/" in line or "/sys/" in line) and not any(fs in line for fs in ("f2fs", "F2FS", "ext4", "EXT4")):
        return None

    # Keep noise down: these strings can happen a lot; scope to /data or dm- to stay relevant.
    noisy = ("Permission denied", "No such file or directory", "open failed:")
    if any(n in line for n in noisy):
        if "/data/" not in line and "dev=\"dm-" not in line:
            return None

    redacted = _RE_REDACT_DATA_APP.sub("/data/app/<redacted>", line)
    return redacted


def _maybe_avc_denial(line: str) -> Optional[str]:
    if "avc:" not in line or "denied" not in line:
        return None
    if "/data/" not in line and "dev=\"dm-" not in line and "vendor_log_file" not in line:
        return None
    return _RE_REDACT_DATA_APP.sub("/data/app/<redacted>", line)


def _print_top(title: str, counter: Counter[str], limit: int) -> None:
    print(f"\n== {title} (top {limit}) ==")
    if not counter:
        print("(none)")
        return
    for k, v in counter.most_common(limit):
        print(f"{v:6d}  {k}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline triage for storage-related signals in a logcat_all.txt.")
    ap.add_argument("--log", required=True, type=Path, help="Path to logcat_all.txt")
    ap.add_argument("--top", type=int, default=40, help="Top-N lines per section")
    ap.add_argument(
        "--focus",
        type=str,
        default="",
        help="Optional package/process substring (e.g. com.UCMobile) to print nearby lines for",
    )
    ap.add_argument("--focus-context", type=int, default=40, help="Lines to print after each focus hit")
    args = ap.parse_args()

    crash_sigs: Counter[str] = Counter()
    storage_lines: Counter[str] = Counter()
    avc_denials: Counter[str] = Counter()

    focus_hits = 0
    focus_budget = 0

    for idx, line in enumerate(_iter_lines(args.log), start=1):
        sig = _parse_am_crash(line)
        if sig is not None:
            crash_sigs[sig.key()] += 1

        st = _maybe_storage_line(line)
        if st is not None:
            storage_lines[st] += 1

        avc = _maybe_avc_denial(line)
        if avc is not None:
            avc_denials[avc] += 1

        if args.focus:
            if args.focus in line:
                focus_hits += 1
                focus_budget = args.focus_context
                print(f"\n-- focus hit #{focus_hits} at line {idx} --\n{line}")
                continue
            if focus_budget > 0:
                print(line)
                focus_budget -= 1

    print(f"Log: {args.log}")
    print(f"Crash signatures (am_crash): {len(crash_sigs)} unique")
    print(f"Storage-ish lines: {len(storage_lines)} unique")
    print(f"SELinux avc denials (filtered): {len(avc_denials)} unique")
    if args.focus:
        print(f"Focus hits: {focus_hits} (context {args.focus_context} lines each)")

    _print_top("am_crash signatures", crash_sigs, args.top)
    _print_top("storage-ish lines", storage_lines, args.top)
    _print_top("SELinux avc denials", avc_denials, args.top)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
