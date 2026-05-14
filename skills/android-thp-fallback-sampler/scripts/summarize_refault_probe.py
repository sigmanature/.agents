#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


TRACE_MARKER_RE = re.compile(r"(\d+\.\d+):\s+tracing_mark_write:\s+(.*)")
FAULT_BEGIN_RE = re.compile(
    r"(\d+\.\d+):\s+filemap_fault_begin:.*?ino=([0-9a-fA-F]+).*?pgoff=([0-9a-fA-F]+)"
    r".*?address=([0-9a-fA-F]+).*?mm=([0-9a-fA-F]+).*?tgid=(\d+)"
)
WAIT_START_RE = re.compile(
    r"(\d+\.\d+):\s+filemap_fault_wait_start:.*?ino=([0-9a-fA-F]+).*?pgoff=([0-9a-fA-F]+)"
    r".*?mm=([0-9a-fA-F]+).*?tgid=(\d+)"
)
WAIT_END_RE = re.compile(
    r"(\d+\.\d+):\s+filemap_fault_wait_end:.*?ino=([0-9a-fA-F]+).*?pgoff=([0-9a-fA-F]+)"
    r".*?mm=([0-9a-fA-F]+).*?tgid=(\d+)"
)
VICTIM_END_RE = re.compile(r"memstress:victim_revisit:end package=(\S+) ok=(\d+)")


def _open_trace(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def iter_trace_paths(trace_dir: Path) -> List[Path]:
    return sorted(trace_dir.glob("trace_stream_*.txt")) + sorted(trace_dir.glob("trace_stream_*.txt.gz"))


def iter_trace_lines(trace_dir: Path, paths: Optional[Sequence[Path]] = None) -> Iterator[str]:
    files = list(paths) if paths is not None else iter_trace_paths(trace_dir)
    for path in files:
        with _open_trace(path) as f:
            for line in f:
                yield line.rstrip("\n")


def _trace_path_with_matches(trace_dir: Path, pattern: str) -> List[Path]:
    matched: List[Path] = []
    for path in iter_trace_paths(trace_dir):
        with _open_trace(path) as f:
            for line in f:
                if pattern in line:
                    matched.append(path)
                    break
    return matched


def parse_victim_windows(trace_dir: Path, post_window_s: float) -> List[Dict]:
    pending: Dict[str, Dict] = {}
    windows: List[Dict] = []
    marker_paths = _trace_path_with_matches(trace_dir, "memstress:victim_")
    for line in iter_trace_lines(trace_dir, marker_paths):
        m = TRACE_MARKER_RE.search(line)
        if not m:
            continue
        ts = float(m.group(1))
        msg = m.group(2)
        if not msg.startswith("memstress:victim_"):
            continue
        parts = msg.split()
        label = parts[0]
        fields = {}
        for token in parts[1:]:
            if "=" in token:
                k, v = token.split("=", 1)
                fields[k] = v
        if label.endswith(":begin"):
            key = label.rsplit(":", 1)[0] + "_" + fields.get("package", "unknown")
            pending[key] = {"kind": key.split(":")[-1].rsplit("_", 1)[0], "begin_ts": ts, "fields": fields}
        elif label.endswith(":end"):
            key = label.rsplit(":", 1)[0] + "_" + fields.get("package", "unknown")
            item = pending.pop(key, None)
            if item is None:
                item = {"kind": key.split(":")[-1].rsplit("_", 1)[0], "begin_ts": ts, "fields": fields}
            item["end_ts"] = ts
            item["window_end_ts"] = ts + max(0.0, float(post_window_s))
            windows.append(item)
    return windows


def read_cycle_log_victim_revisits(run_dir: Path) -> List[Dict]:
    path = run_dir / "memstress" / "cycle_log.jsonl"
    out: List[Dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("event") != "victim_revisit":
                continue
            out.append(row)
    return out


def collect_victim_end_markers(trace_dir: Path) -> List[Dict]:
    out: List[Dict] = []
    marker_paths = _trace_path_with_matches(trace_dir, "memstress:victim_revisit:end")
    for line in iter_trace_lines(trace_dir, marker_paths):
        m = TRACE_MARKER_RE.search(line)
        if not m:
            continue
        ts = float(m.group(1))
        msg = m.group(2)
        em = VICTIM_END_RE.search(msg)
        if not em:
            continue
        package, ok = em.groups()
        out.append({"end_ts": ts, "package": package, "ok": int(ok)})
    return out


def augment_victim_windows_from_cycle_log(
    *,
    run_dir: Path,
    trace_dir: Path,
    windows: Sequence[Dict],
    post_window_s: float,
) -> List[Dict]:
    cycle_rows = read_cycle_log_victim_revisits(run_dir)
    if not cycle_rows:
        return list(windows)

    end_markers = collect_victim_end_markers(trace_dir)
    if not end_markers:
        return list(windows)

    combined = list(windows)
    existing_end_ts = {round(w["end_ts"], 6) for w in combined if "end_ts" in w}

    for row, marker in zip(cycle_rows, end_markers):
        if round(marker["end_ts"], 6) in existing_end_ts:
            continue
        hold_ms = int(row.get("hold_ms", 0))
        end_ts = float(marker["end_ts"])
        begin_ts = max(0.0, end_ts - max(0.1, hold_ms / 1000.0))
        pkg = row.get("package", marker["package"])
        combined.append(
            {
                "kind": "victim_revisit",
                "begin_ts": begin_ts,
                "end_ts": end_ts,
                "window_end_ts": end_ts + max(0.0, float(post_window_s)),
                "fields": {
                    "package": pkg,
                    "cycle": row.get("cycle"),
                    "source": "cycle_log+trace_end",
                },
            }
        )
    combined.sort(key=lambda w: (w.get("begin_ts", 0.0), w.get("end_ts", 0.0)))
    return combined


def _fault_key(mm: str, tgid: str, ino: str, pgoff: str) -> Tuple[str, str, str, str]:
    return (mm.lower(), str(tgid), ino.lower(), pgoff.lower())


def _analyze_refault_for_windows(trace_dir: Path, windows: Sequence[Dict]) -> Dict:
    revisit_windows = [w for w in windows if w.get("kind") == "victim_revisit"]
    if not revisit_windows:
        return {
            "victim_revisit_windows": 0,
            "faults_per_window": [],
            "waits_per_window": [],
            "repeated_page_keys": 0,
            "repeated_page_key_samples": [],
            "refault_candidate": False,
        }

    window_faults: List[Counter] = [Counter() for _ in revisit_windows]
    waits_per_window = [0 for _ in revisit_windows]
    fault_counts = [0 for _ in revisit_windows]

    relevant_paths = _trace_path_with_matches(trace_dir, "filemap_fault_")
    for line in iter_trace_lines(trace_dir, relevant_paths):
        mb = FAULT_BEGIN_RE.search(line)
        if mb:
            ts, ino, pgoff, _address, mm, tgid = mb.groups()
            tsf = float(ts)
            for idx, w in enumerate(revisit_windows):
                if w["begin_ts"] <= tsf <= w["window_end_ts"]:
                    key = _fault_key(mm, tgid, ino, pgoff)
                    window_faults[idx][key] += 1
                    fault_counts[idx] += 1
            continue

        mw = WAIT_START_RE.search(line)
        if mw:
            tsf = float(mw.group(1))
            for idx, w in enumerate(revisit_windows):
                if w["begin_ts"] <= tsf <= w["window_end_ts"]:
                    waits_per_window[idx] += 1

    appearance = Counter()
    for counter in window_faults:
        for key in counter:
            appearance[key] += 1

    repeated = [key for key, count in appearance.items() if count >= 2]
    samples = [
        {"mm": mm, "tgid": tgid, "ino": ino, "pgoff": pgoff, "windows": appearance[(mm, tgid, ino, pgoff)]}
        for mm, tgid, ino, pgoff in repeated[:10]
    ]
    return {
        "victim_revisit_windows": len(revisit_windows),
        "faults_per_window": fault_counts,
        "waits_per_window": waits_per_window,
        "repeated_page_keys": len(repeated),
        "repeated_page_key_samples": samples,
        "refault_candidate": len(revisit_windows) >= 2 and len(repeated) > 0,
    }


def analyze_refault(trace_dir: Path, windows: Sequence[Dict]) -> Dict:
    by_package: Dict[str, List[Dict]] = defaultdict(list)
    for w in windows:
        pkg = w.get("fields", {}).get("package", "unknown")
        by_package[pkg].append(w)

    if not by_package:
        return {"by_package": {}, "overall": _analyze_refault_for_windows(trace_dir, [])}

    overall_windows = []
    per_package: Dict[str, Dict] = {}
    for pkg, pkg_windows in by_package.items():
        per_package[pkg] = _analyze_refault_for_windows(trace_dir, pkg_windows)
        overall_windows.extend(pkg_windows)

    return {
        "by_package": per_package,
        "overall": _analyze_refault_for_windows(trace_dir, overall_windows),
    }


def latest_trace_file(trace_dir: Path) -> Optional[Path]:
    traces = sorted(trace_dir.glob("trace_stream_*.txt"), key=lambda p: p.stat().st_mtime)
    return traces[-1] if traces else None


def run_contention_summary(trace_dir: Path, analyzer: Path) -> Dict:
    traces = sorted(trace_dir.glob("trace_stream_*.txt"), key=lambda p: p.stat().st_mtime)
    if not traces:
        return {"status": "no_trace"}
    traces = sorted(traces, key=lambda p: p.stat().st_size)[:3]
    trace_file = traces[0]
    try:
        cp = subprocess.run(
            [sys.executable, str(analyzer), str(trace_file)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as e:
        return {"status": "error", "error": str(e), "trace_file": str(trace_file)}

    summary_path = trace_file.with_suffix(".summary.json")
    data: Dict = {
        "status": "ran",
        "trace_file": str(trace_file),
        "returncode": cp.returncode,
        "stdout_tail": (cp.stdout or "")[-2000:],
        "stderr_tail": (cp.stderr or "")[-2000:],
    }
    if summary_path.exists():
        try:
            data["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as e:
            data["summary_load_error"] = str(e)
    return data


def build_report(*, run_dir: Path, trace_dir: Path, analyzer: Path, post_window_s: float) -> Dict:
    windows = parse_victim_windows(trace_dir, post_window_s=post_window_s)
    windows = augment_victim_windows_from_cycle_log(
        run_dir=run_dir,
        trace_dir=trace_dir,
        windows=windows,
        post_window_s=post_window_s,
    )
    refault = analyze_refault(trace_dir, windows)
    contention = run_contention_summary(trace_dir, analyzer)
    return {
        "generated_at": int(time.time()),
        "run_dir": str(run_dir),
        "trace_dir": str(trace_dir),
        "victim_windows": windows,
        "refault": refault,
        "contention": contention,
    }


def _write_refault_section(md: List[str], refault: Dict, title: str) -> None:
    md.append(f"## {title}")
    md.append("")
    md.append(f"- victim_revisit_windows: {refault['victim_revisit_windows']}")
    md.append(f"- refault_candidate: {'YES' if refault['refault_candidate'] else 'NO'}")
    md.append(f"- faults_per_window: {refault['faults_per_window']}")
    md.append(f"- waits_per_window: {refault['waits_per_window']}")
    md.append(f"- repeated_page_keys: {refault['repeated_page_keys']}")
    md.append("")
    md.append("### Repeated Page Key Samples")
    if refault["repeated_page_key_samples"]:
        for item in refault["repeated_page_key_samples"]:
            md.append(
                f"- mm={item['mm']} tgid={item['tgid']} ino={item['ino']} pgoff={item['pgoff']} windows={item['windows']}"
            )
    else:
        md.append("- none")
    md.append("")


def write_outputs(report: Dict, out_json: Path, out_md: Path) -> None:
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refault = report["refault"]
    by_package = refault.get("by_package", {})
    overall = refault.get("overall", refault)
    contention = report["contention"]
    contention_summary = contention.get("summary", {})
    md = [
        "# Refault Probe Summary",
        "",
        f"- generated_at: {report['generated_at']}",
    ]

    if by_package:
        md.append(f"- packages_analyzed: {list(by_package.keys())}")
        md.append("")
        for pkg, pkg_refault in by_package.items():
            _write_refault_section(md, pkg_refault, f"Package: {pkg}")
        _write_refault_section(md, overall, "Overall")
    else:
        _write_refault_section(md, overall, "Overall")

    md.extend(
        [
            "## Contention Snapshot",
            f"- status: {contention.get('status')}",
            f"- trace_file: {contention.get('trace_file', '')}",
        ]
    )
    if contention_summary:
        md.extend(
            [
                f"- contention_chains: {contention_summary.get('contention_chains')}",
                f"- by_syscall: {contention_summary.get('by_syscall')}",
                f"- wait_stats: {contention_summary.get('wait_stats')}",
            ]
        )
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize refault-probe outputs into compact JSON/Markdown")
    p.add_argument("--run-dir", required=True, help="Top-level probe output directory")
    p.add_argument("--trace-dir", default=None, help="Trace directory (default: <run-dir>/trace)")
    p.add_argument(
        "--contention-analyzer",
        default="/home/nzzhao/.agents/skills/mmap-lock-contention-analysis/scripts/analyze_contention_v2.py",
        help="Path to contention analyzer script",
    )
    p.add_argument("--post-window-s", type=float, default=1.5, help="Extra time after victim marker end to include in victim window")
    p.add_argument("--out-json", default=None, help="Summary JSON path (default: <run-dir>/probe_summary.json)")
    p.add_argument("--out-md", default=None, help="Summary Markdown path (default: <run-dir>/probe_summary.md)")
    p.add_argument("--watch", action="store_true", help="Repeat summary generation until --until-pid exits")
    p.add_argument("--every-s", type=float, default=10.0, help="Watch interval seconds")
    p.add_argument("--until-pid", type=int, default=0, help="If set with --watch, stop when this host pid exits")
    return p.parse_args(argv)


def run_once(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    trace_dir = Path(args.trace_dir).resolve() if args.trace_dir else run_dir / "trace"
    out_json = Path(args.out_json).resolve() if args.out_json else run_dir / "probe_summary.json"
    out_md = Path(args.out_md).resolve() if args.out_md else run_dir / "probe_summary.md"
    report = build_report(
        run_dir=run_dir,
        trace_dir=trace_dir,
        analyzer=Path(args.contention_analyzer).resolve(),
        post_window_s=float(args.post_window_s),
    )
    write_outputs(report, out_json, out_md)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.watch:
        run_once(args)
        return 0

    while True:
        run_once(args)
        if args.until_pid and not pid_alive(int(args.until_pid)):
            return 0
        time.sleep(max(1.0, float(args.every_s)))


if __name__ == "__main__":
    raise SystemExit(main())
