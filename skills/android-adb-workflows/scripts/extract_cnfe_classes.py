#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


DESCRIPTOR_RE = re.compile(r"\b(L[a-zA-Z_$][a-zA-Z0-9_$/\$]*;)")
FAILED_RESOLUTION_RE = re.compile(r"Failed resolution of:\s*(L[a-zA-Z_$][a-zA-Z0-9_$/\$]*;)")
CNFE_DOT_RE = re.compile(
    r"(?:ClassNotFoundException|NoClassDefFoundError):\s+"
    r"((?:[A-Za-z_$][A-Za-z0-9_$]*\.)+[A-Za-z_$][A-Za-z0-9_$]*)"
)
DIDNT_FIND_RE = re.compile(
    r"""Didn't find class ["']((?:[A-Za-z_$][A-Za-z0-9_$]*\.)+[A-Za-z_$][A-Za-z0-9_$]*)["']"""
)
AM_CRASH_RE = re.compile(r"am_crash:\s*\[(.*)\]")
PROCESS_RE = re.compile(r"Process:\s+([A-Za-z0-9._$:-]+)")
THREADTIME_PID_RE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+(\d+)\s+\d+\s+[VDIWEAF]\s+[^:]+:")


def normalize_class_name(raw: str) -> str | None:
    candidate = raw.strip().strip("\"'")
    if not candidate:
        return None
    if candidate.startswith("L") and candidate.endswith(";"):
        candidate = candidate[1:-1].replace("/", ".")
    if "/" in candidate and "." not in candidate:
        candidate = candidate.replace("/", ".")
    if "." not in candidate:
        return None
    return candidate


def extract_matches(line: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []

    for match in FAILED_RESOLUTION_RE.finditer(line):
        normalized = normalize_class_name(match.group(1))
        if normalized:
            matches.append(("failed_resolution", normalized))

    for match in DIDNT_FIND_RE.finditer(line):
        normalized = normalize_class_name(match.group(1))
        if normalized:
            matches.append(("didnt_find_class", normalized))

    for match in CNFE_DOT_RE.finditer(line):
        normalized = normalize_class_name(match.group(1))
        if normalized:
            matches.append(("exception_name", normalized))

    am_crash_match = AM_CRASH_RE.search(line)
    if am_crash_match:
        payload = [item.strip() for item in am_crash_match.group(1).split(",")]
        for index, value in enumerate(payload[:-1]):
            if value in {"java.lang.ClassNotFoundException", "java.lang.NoClassDefFoundError"}:
                normalized = normalize_class_name(payload[index + 1])
                if normalized:
                    matches.append(("am_crash", normalized))

    if not matches and ("ClassNotFoundException" in line or "NoClassDefFoundError" in line):
        for match in DESCRIPTOR_RE.finditer(line):
            normalized = normalize_class_name(match.group(1))
            if normalized:
                matches.append(("descriptor_fallback", normalized))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in matches:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def process_matches_package(process_name: str, package: str) -> bool:
    return process_name == package or process_name.startswith(f"{package}:")


def extract_process_name(line: str) -> str | None:
    match = PROCESS_RE.search(line)
    if not match:
        return None
    return match.group(1)


def extract_threadtime_pid(line: str) -> str | None:
    match = THREADTIME_PID_RE.match(line)
    if not match:
        return None
    return match.group(1)


def am_crash_matches_package(line: str, package: str) -> bool | None:
    match = AM_CRASH_RE.search(line)
    if not match:
        return None
    payload = [item.strip() for item in match.group(1).split(",")]
    if len(payload) < 3:
        return False
    return process_matches_package(payload[2], package)


def line_is_relevant_for_package(line: str, package: str, relevant_pids: set[str]) -> bool:
    am_crash_match = am_crash_matches_package(line, package)
    if am_crash_match is not None:
        return am_crash_match

    process_name = extract_process_name(line)
    if process_name:
        pid = extract_threadtime_pid(line)
        if process_matches_package(process_name, package):
            if pid:
                relevant_pids.add(pid)
            return True
        return False

    if package in line:
        return True

    pid = extract_threadtime_pid(line)
    return pid is not None and pid in relevant_pids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract and normalize CNFE/NCDFE-related class names from Android logcat."
    )
    parser.add_argument("logcat", type=Path, help="logcat -v threadtime file")
    parser.add_argument(
        "--classes-only",
        action="store_true",
        help="print only normalized class names, one per line",
    )
    parser.add_argument(
        "--package",
        help="only keep matches associated with this package's crash/process context",
    )
    parser.add_argument(
        "--start-line",
        type=int,
        default=1,
        help="ignore logcat lines before this 1-based line number",
    )
    args = parser.parse_args()

    if args.start_line < 1:
        parser.error("--start-line must be >= 1")

    matches: list[dict[str, object]] = []
    classes: set[str] = set()
    relevant_pids: set[str] = set()

    with args.logcat.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            if line_no < args.start_line:
                continue
            line = raw_line.rstrip("\n")
            if args.package and not line_is_relevant_for_package(line, args.package, relevant_pids):
                continue
            for kind, normalized in extract_matches(line):
                classes.add(normalized)
                matches.append(
                    {
                        "line_no": line_no,
                        "kind": kind,
                        "class": normalized,
                        "raw_line": line,
                    }
                )

    ordered_classes = sorted(classes)
    if args.classes_only:
        for class_name in ordered_classes:
            print(class_name)
        return 0

    payload = {
        "classes": ordered_classes,
        "matches": matches,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
