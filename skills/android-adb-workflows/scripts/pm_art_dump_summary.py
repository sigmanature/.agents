#!/usr/bin/env python3

import argparse
import json
import re
import sys
from pathlib import Path


PACKAGE_RE = re.compile(r"^Package \[(?P<package>[^\]]+)\]")
STATUS_RE = re.compile(
    r"^\s*(?P<abi>[^:\s]+):\s+"
    r"\[status=(?P<status>[^\]]+)\]\s+"
    r"\[reason=(?P<reason>[^\]]+)\]"
    r"(?P<flags>.*)$"
)
FLAG_RE = re.compile(r"\[(?P<flag>[^\]]+)\]")
LOCATION_RE = re.compile(r"^\s*\[location is (?P<location>.+)\]\s*$")


def parse_pm_art_dump_text(text: str) -> dict:
    package = None
    entries: list[dict] = []
    current: dict | None = None

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        package_match = PACKAGE_RE.match(line)
        if package_match:
            package = package_match.group("package")
            current = None
            continue

        status_match = STATUS_RE.match(line)
        if status_match:
            flags = [flag_match.group("flag") for flag_match in FLAG_RE.finditer(status_match.group("flags"))]
            current = {
                "line_no": line_no,
                "abi": status_match.group("abi"),
                "status": status_match.group("status"),
                "reason": status_match.group("reason"),
                "flags": flags,
                "location": None,
            }
            entries.append(current)
            continue

        location_match = LOCATION_RE.match(line)
        if location_match and current is not None:
            current["location"] = location_match.group("location")

    return {
        "package": package,
        "entries": entries,
        "status_by_abi": {entry["abi"]: entry["status"] for entry in entries},
        "reason_by_abi": {entry["abi"]: entry["reason"] for entry in entries},
    }


def render_text(summary: dict) -> str:
    lines = [f"package: {summary.get('package') or '<unknown>'}"]
    for entry in summary["entries"]:
        flags = f" flags={','.join(entry['flags'])}" if entry["flags"] else ""
        location = f" location={entry['location']}" if entry["location"] else ""
        lines.append(
            f"{entry['abi']}: effective_filter={entry['status']} reason={entry['reason']}{flags}{location}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize `pm art dump <pkg>` into machine-readable effective status/reason/location records."
    )
    parser.add_argument("path", type=Path, help="Path to captured `pm art dump` text")
    parser.add_argument("--text", action="store_true", help="Emit human-readable text instead of JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        text = args.path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = parse_pm_art_dump_text(text)
    if args.text:
        print(render_text(summary))
    else:
        print(json.dumps(summary, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
