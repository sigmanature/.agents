#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derive per-window deltas and fallback ratios from raw_samples.csv.

Main ratio:
  fallback_ratio = d_anon_fault_fallback / (d_anon_fault_alloc + d_anon_fault_fallback)

This script is intentionally dependency-light.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Row:
    host_ts: int
    device_ts: Optional[int]
    error: str
    values: Dict[str, Optional[int]]


def _to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    return int(s) if s.isdigit() else None


def read_raw(path: Path) -> List[Row]:
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        counters = [c for c in r.fieldnames or [] if c not in ("host_ts", "device_ts", "error")]
        rows: List[Row] = []
        for d in r:
            host_ts = int(d.get("host_ts") or 0)
            dev_ts = _to_int(d.get("device_ts", ""))
            err = d.get("error", "") or ""
            vals: Dict[str, Optional[int]] = {c: _to_int(d.get(c, "")) for c in counters}
            rows.append(Row(host_ts=host_ts, device_ts=dev_ts, error=err, values=vals))
    return rows


def write_derived(raw: List[Row], out_csv: Path) -> None:
    # Determine counters from first row
    counters = sorted(list(raw[0].values.keys())) if raw else []

    # We'll compute deltas between consecutive samples where both values exist.
    fieldnames = [
        "window_end_host_ts",
        "window_s",
        "attempts",
        "d_anon_fault_alloc",
        "d_anon_fault_fallback",
        "fallback_ratio",
        "errors_in_window",
    ] + [f"d_{c}" for c in counters if c not in ("anon_fault_alloc", "anon_fault_fallback")]

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        prev = raw[0]
        prev_ts = prev.host_ts
        window_err = 1 if prev.error else 0

        for cur in raw[1:]:
            dt = max(1, cur.host_ts - prev_ts)
            # compute deltas
            deltas: Dict[str, Optional[int]] = {}
            for c in counters:
                a = prev.values.get(c)
                b = cur.values.get(c)
                if a is None or b is None:
                    deltas[c] = None
                else:
                    deltas[c] = b - a

            d_alloc = deltas.get("anon_fault_alloc")
            d_fallback = deltas.get("anon_fault_fallback")
            attempts = None
            ratio = None
            if d_alloc is not None and d_fallback is not None:
                attempts = d_alloc + d_fallback
                if attempts > 0:
                    ratio = d_fallback / attempts

            window_err += 1 if cur.error else 0

            row = {
                "window_end_host_ts": cur.host_ts,
                "window_s": dt,
                "attempts": attempts if attempts is not None else "",
                "d_anon_fault_alloc": d_alloc if d_alloc is not None else "",
                "d_anon_fault_fallback": d_fallback if d_fallback is not None else "",
                "fallback_ratio": f"{ratio:.6f}" if ratio is not None else "",
                "errors_in_window": window_err,
            }

            for c in counters:
                if c in ("anon_fault_alloc", "anon_fault_fallback"):
                    continue
                v = deltas.get(c)
                row[f"d_{c}"] = v if v is not None else ""

            w.writerow(row)

            # advance
            prev = cur
            prev_ts = cur.host_ts
            window_err = 0


# ---------- vmstat summary helpers ----------

VMSTAT_ALLOCSTALL_KEYS = ("allocstall_normal", "allocstall_movable")
VMSTAT_COMPACT_KEYS = ("compact_stall",)


def read_vmstat_json(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: Dict[str, int] = {}
    for k, v in data.items():
        try:
            result[k] = int(v)
        except (TypeError, ValueError):
            pass
    return result


def compute_vmstat_delta(start_path: Path, end_path: Path) -> Dict[str, Optional[int]]:
    """Return end - start for the general vmstat metrics shown in summary.md."""
    start = read_vmstat_json(start_path)
    end = read_vmstat_json(end_path)
    if not start or not end:
        return {}

    def _get(d: Dict[str, int], keys: tuple) -> int:
        return sum(d.get(k, 0) for k in keys)

    alloc_stall_start = _get(start, VMSTAT_ALLOCSTALL_KEYS)
    alloc_stall_end = _get(end, VMSTAT_ALLOCSTALL_KEYS)
    compact_stall_start = _get(start, VMSTAT_COMPACT_KEYS)
    compact_stall_end = _get(end, VMSTAT_COMPACT_KEYS)

    return {
        "alloc_stall": alloc_stall_end - alloc_stall_start,
        "compact_stall": compact_stall_end - compact_stall_start,
    }


def write_summary(derived_csv: Path, out_md: Path,
                  vmstat_delta: Optional[Dict[str, int]] = None) -> None:
    alloc_total = 0
    fallback_total = 0
    attempts_total = 0

    with derived_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for d in r:
            a = (d.get("d_anon_fault_alloc") or "").strip()
            fb = (d.get("d_anon_fault_fallback") or "").strip()
            at = (d.get("attempts") or "").strip()
            if a.lstrip("-").isdigit():
                alloc_total += int(a)
            if fb.lstrip("-").isdigit():
                fallback_total += int(fb)
            if at.lstrip("-").isdigit():
                attempts_total += int(at)

    overall_ratio = (fallback_total / attempts_total) if attempts_total > 0 else None

    vmstat_delta = vmstat_delta or {}
    alloc_stall = vmstat_delta.get("alloc_stall")
    compact_stall = vmstat_delta.get("compact_stall")

    def _fmt(v: Optional[int]) -> str:
        if v is None:
            return "N/A"
        return str(v)

    ratio_str = f"{overall_ratio:.6f}" if overall_ratio is not None else "N/A"

    lines: List[str] = [
        "# THP 16KB Anon Fallback Summary\n",
        "## General metrics (end - start)\n",
        "| metric | value |",
        "|--------|-------|",
        f"| anon_alloc | {alloc_total} |",
        f"| anon_fallback | {fallback_total} |",
        f"| fallback_ratio | {ratio_str} |",
        f"| alloc_stall | {_fmt(alloc_stall)} |",
        f"| compact_stall | {_fmt(compact_stall)} |\n",
        "- **anon_alloc**: total `anon_fault_alloc` during the experiment (THP stats end - start).",
        "- **anon_fallback**: total `anon_fault_fallback` during the experiment (THP stats end - start).",
        "- **fallback_ratio**: `anon_fallback / (anon_alloc + anon_fallback)`.",
        "- **alloc_stall**: `allocstall_normal + allocstall_movable` from `/proc/vmstat` (end - start).",
        "- **compact_stall**: `compact_stall` from `/proc/vmstat` (end - start).",
        "",
        "Per-window deltas are available in `derived.csv` and `vmstat_derived.csv`.",
    ]

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="derive deltas and fallback ratios from raw_samples.csv")
    p.add_argument("raw_csv", help="Path to raw_samples.csv")
    p.add_argument("--out-dir", default=None, help="Output dir (default: same dir as raw)")
    p.add_argument("--vmstat-start", default=None, help="Path to vmstat_start.json")
    p.add_argument("--vmstat-end", default=None, help="Path to vmstat_end.json")

    args = p.parse_args(argv)

    raw_path = Path(args.raw_csv)
    if not raw_path.exists():
        raise FileNotFoundError(str(raw_path))

    out_dir = Path(args.out_dir) if args.out_dir else raw_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = read_raw(raw_path)
    if len(raw) < 2:
        (out_dir / "summary.md").write_text("# THP 16KB Anon Fallback Summary\n\nNot enough samples.\n", encoding="utf-8")
        return 0

    derived_csv = out_dir / "derived.csv"
    write_derived(raw, derived_csv)

    vmstat_delta = None
    if args.vmstat_start and args.vmstat_end:
        vmstat_delta = compute_vmstat_delta(Path(args.vmstat_start), Path(args.vmstat_end))

    summary_md = out_dir / "summary.md"
    write_summary(derived_csv, summary_md, vmstat_delta)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
