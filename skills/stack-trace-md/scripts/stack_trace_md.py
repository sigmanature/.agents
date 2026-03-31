#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple

TASK_PAT = re.compile(r"task:(?P<task>\S+)\s+state:(?P<state>\S+).*?pid:(?P<pid>\d+)")
FUNC_PAT = re.compile(r"(?:\[\s*[0-9.]+\]\s+)?(?:\[<[^>]+>\]\s+)?([A-Za-z0-9_.$]+)\+[^\s]+")

REG_PREFIXES = (
    "pc :", "lr :", "sp :", "x0 :", "x1 :", "x2 :", "x3 :", "x4 :", "x5 :", "x6 :", "x7 :", "x8 :", "x9 :",
)

CRASH_MARKERS = ("BUG:", "Oops:", "Unable to handle", "Kernel panic", "panic")


def load_lines(source: str) -> List[str]:
    if source == "-":
        return sys.stdin.read().splitlines()
    return Path(source).read_text(errors="replace").splitlines()


def is_func_line(s: str) -> bool:
    return bool(FUNC_PAT.search(s))


def is_trace_payload(s: str) -> bool:
    t = s.strip()
    return is_func_line(t) or t.startswith(REG_PREFIXES)


def capture_trace(lines: List[str], i: int) -> Tuple[List[str], int]:
    out = [lines[i]]
    j = i + 1
    while j < len(lines):
        s = lines[j]
        if not s.strip():
            break
        if is_trace_payload(s):
            out.append(s)
            j += 1
            continue
        if s.strip().startswith("task:"):
            out.append(s)
            j += 1
            continue
        break
    return out, j


def extract_incidents(lines: List[str], max_lookahead: int = 80) -> List[Dict[str, Any]]:
    incidents: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if "task:" in line and "state:" in line and "pid:" in line:
            m = TASK_PAT.search(line)
            task_meta = {
                "task": m.group("task") if m else "?",
                "state": m.group("state") if m else "?",
                "pid": m.group("pid") if m else "?",
                "line": i + 1,
            }
            trace: List[str] = []
            end = i + 1
            j = i + 1
            while j < min(len(lines), i + max_lookahead):
                if "Call trace:" in lines[j]:
                    trace, end = capture_trace(lines, j)
                    break
                j += 1
            incidents.append({"type": "blocked-task", "task": task_meta, "trace": trace})
            i = end
            continue

        if any(marker in line for marker in CRASH_MARKERS):
            header = [line]
            trace = []
            end = i + 1
            j = i + 1
            while j < min(len(lines), i + max_lookahead):
                if "Call trace:" in lines[j]:
                    trace, end = capture_trace(lines, j)
                    break
                if any(marker in lines[j] for marker in CRASH_MARKERS):
                    header.append(lines[j])
                j += 1
            incidents.append({"type": "crash", "line": i + 1, "header": header, "trace": trace})
            i = end
            continue

        if "Call trace:" in line:
            trace, end = capture_trace(lines, i)
            incidents.append({"type": "trace-only", "line": i + 1, "trace": trace})
            i = end
            continue

        i += 1

    return incidents


def trace_functions(trace_lines: List[str]) -> List[str]:
    out: List[str] = []
    for ln in trace_lines:
        m = FUNC_PAT.search(ln)
        if m:
            out.append(m.group(1))
    return out


def to_markdown(source_label: str, incidents: List[Dict[str, Any]]) -> str:
    md: List[str] = []
    md.append(f"# Stack Report: `{source_label}`")
    md.append("")
    md.append(f"- Incidents: **{len(incidents)}**")
    md.append("")

    if not incidents:
        md.append("No matching crash, blocked-task, or call-trace incidents were detected.")
        md.append("")
        return "\n".join(md)

    for idx, inc in enumerate(incidents, 1):
        md.append(f"## {idx}. {inc.get('type', 'unknown')}")
        if inc.get("type") == "blocked-task":
            task = inc["task"]
            md.append(
                f"- Task: `{task['task']}`  PID: `{task['pid']}`  State: `{task['state']}`  Line: `{task['line']}`"
            )
        elif "line" in inc:
            md.append(f"- Line: `{inc['line']}`")

        funcs = trace_functions(inc.get("trace", []))
        if funcs:
            md.append("- Call Chain:")
            for n, fn in enumerate(funcs, 1):
                md.append(f"  {n}. `{fn}`")

        if inc.get("header"):
            md.append("")
            md.append("```text")
            md.extend(inc["header"])
            md.append("```")

        if inc.get("trace"):
            md.append("")
            md.append("```text")
            md.extend(inc["trace"])
            md.append("```")

        md.append("")

    return "\n".join(md)


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract crash/deadlock call traces and render Markdown")
    ap.add_argument("log", nargs="?", default="-", help="Path to log file, or '-' for stdin")
    ap.add_argument("-o", "--output", help="Write markdown to file instead of stdout")
    ap.add_argument("--title", help="Override source label shown in report title")
    ap.add_argument("--max-lookahead", type=int, default=80, help="How many lines ahead to search for a Call trace")
    ns = ap.parse_args()

    if ns.log != "-":
        src = Path(ns.log)
        if not src.exists():
            raise SystemExit(f"log not found: {src}")
        source_label = ns.title or str(src)
    else:
        source_label = ns.title or "stdin"

    lines = load_lines(ns.log)
    incidents = extract_incidents(lines, max_lookahead=ns.max_lookahead)
    md = to_markdown(source_label, incidents)

    if ns.output:
        Path(ns.output).write_text(md)
    else:
        print(md)


if __name__ == "__main__":
    main()
