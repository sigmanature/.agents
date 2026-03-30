#!/usr/bin/env python3
"""Extract the most likely crash lookup target from a Linux kernel panic/oops log.

Supports:
- frame lines marked with (P)
- tokens like func+0x12/0x40
- PC/RIP style lines with func+offset
- direct func+offset tokens embedded in text

Outputs JSON for easy downstream use.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PC_LINE = re.compile(r"(?im)^\s*(?:PC is at|pc\s*:|rip:?|RIP:).*?\b([A-Za-z_][A-Za-z0-9_\.]*\+0x[0-9a-fA-F]+)(?:/0x[0-9a-fA-F]+)?\b.*$")
CALL_TRACE_LINE = re.compile(r"(?im)^.*\b([A-Za-z_][A-Za-z0-9_\.]*\+0x[0-9a-fA-F]+)/(?:0x[0-9a-fA-F]+)\b.*$")
ANY_SYMBOL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_\.]*\+0x[0-9a-fA-F]+)(?:/0x[0-9a-fA-F]+)?\b")
ABSOLUTE_ADDR = re.compile(r"\b0x[0-9a-fA-F]{8,16}\b")


def load_text(path: str | None, text: str | None) -> str:
    if text:
        return text
    if path:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return sys.stdin.read()


def extract_target(text: str) -> dict[str, str | list[str] | None]:
    candidates: list[str] = []

    for line in text.splitlines():
        if '(P)' in line:
            match = ANY_SYMBOL.search(line)
            if match:
                symbol = match.group(1)
                candidates.extend(_other_candidates(text, symbol))
                return {
                    "picked": symbol,
                    "reason": "p-marked frame",
                    "alternates": candidates[:5],
                }

    match = PC_LINE.search(text)
    if match:
        symbol = match.group(1)
        candidates.extend(_other_candidates(text, symbol))
        return {
            "picked": symbol,
            "reason": "pc line",
            "alternates": candidates[:5],
        }

    match = CALL_TRACE_LINE.search(text)
    if match:
        symbol = match.group(1)
        candidates.extend(_other_candidates(text, symbol))
        return {
            "picked": symbol,
            "reason": "call trace frame",
            "alternates": candidates[:5],
        }

    all_symbols = ANY_SYMBOL.findall(text)
    if all_symbols:
        picked = all_symbols[0]
        candidates.extend(_other_candidates(text, picked))
        return {
            "picked": picked,
            "reason": "first symbol+offset token",
            "alternates": candidates[:5],
        }

    addr = ABSOLUTE_ADDR.search(text)
    if addr:
        return {
            "picked": addr.group(0),
            "reason": "first absolute address token",
            "alternates": [],
        }

    return {
        "picked": None,
        "reason": "no symbol or address found",
        "alternates": [],
    }


def _other_candidates(text: str, picked: str) -> list[str]:
    seen = set([picked])
    out = []
    for token in ANY_SYMBOL.findall(text):
        if token not in seen:
            out.append(token)
            seen.add(token)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panic-file", help="path to panic/oops log")
    parser.add_argument("--text", help="panic/oops text provided inline")
    args = parser.parse_args()

    text = load_text(args.panic_file, args.text)
    result = extract_target(text)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["picked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
