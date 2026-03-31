#!/usr/bin/env python3
import argparse
import re
from pathlib import Path

TASK_PAT = re.compile(r"task:(?P<task>\S+)\s+state:(?P<state>\S+).*?pid:(?P<pid>\d+)")
FUNC_PAT = re.compile(r"(?:\[\s*[0-9.]+\]\s+)?(?:\[<[^>]+>\]\s+)?([A-Za-z0-9_.$]+)\+[^\s]+")


REG_PREFIXES = (
    "pc :", "lr :", "sp :", "x0 :", "x1 :", "x2 :", "x3 :", "x4 :", "x5 :", "x6 :", "x7 :", "x8 :", "x9 :",
)


def load_lines(p: Path):
    return p.read_text(errors="replace").splitlines()


def is_func_line(s: str) -> bool:
    return bool(FUNC_PAT.search(s))


def is_trace_payload(s: str) -> bool:
    t = s.strip()
    return is_func_line(t) or t.startswith(REG_PREFIXES)


def capture_trace(lines, i):
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
        # allow one task marker line inside sysrq dump blocks
        if s.strip().startswith("task:"):
            out.append(s)
            j += 1
            continue
        break
    return out, j


def extract_incidents(lines):
    incidents = []
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
            trace = []
            end = i + 1
            j = i + 1
            while j < min(len(lines), i + 80):
                if "Call trace:" in lines[j]:
                    trace, end = capture_trace(lines, j)
                    break
                j += 1
            incidents.append({"type": "blocked-task", "task": task_meta, "trace": trace})
            i = end
            continue

        if any(k in line for k in ["BUG:", "Oops:", "Unable to handle", "Kernel panic", "panic"]):
            header = [line]
            trace = []
            end = i + 1
            j = i + 1
            while j < min(len(lines), i + 80):
                if "Call trace:" in lines[j]:
                    trace, end = capture_trace(lines, j)
                    break
                if any(k in lines[j] for k in ["BUG:", "Oops:", "Unable to handle", "Kernel panic", "panic"]):
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


def trace_functions(trace_lines):
    out = []
    for ln in trace_lines:
        m = FUNC_PAT.search(ln)
        if m:
            out.append(m.group(1))
    return out


def to_markdown(src: Path, incidents):
    md = []
    md.append(f"# Stack Report: `{src}`")
    md.append("")
    md.append(f"- Incidents: **{len(incidents)}**")
    md.append("")

    for idx, inc in enumerate(incidents, 1):
        md.append(f"## {idx}. {inc.get('type','unknown')}")
        if inc.get("type") == "blocked-task":
            t = inc["task"]
            md.append(f"- Task: `{t['task']}`  PID: `{t['pid']}`  State: `{t['state']}`  Line: `{t['line']}`")
        elif "line" in inc:
            md.append(f"- Line: `{inc['line']}`")

        fns = trace_functions(inc.get("trace", []))
        if fns:
            md.append("- Call Chain:")
            for n, fn in enumerate(fns, 1):
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


def main():
    ap = argparse.ArgumentParser(description="Extract crash/deadlock call traces and render Markdown")
    ap.add_argument("log", help="Path to log file")
    ap.add_argument("-o", "--output", help="Write markdown to file")
    ns = ap.parse_args()

    src = Path(ns.log)
    if not src.exists():
        raise SystemExit(f"log not found: {src}")

    lines = load_lines(src)
    incidents = extract_incidents(lines)
    md = to_markdown(src, incidents)

    if ns.output:
        Path(ns.output).write_text(md)
    else:
        print(md)


if __name__ == "__main__":
    main()
