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
import statistics
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


def write_summary(derived_csv: Path, out_md: Path) -> None:
    ratios: List[float] = []
    attempts_total = 0
    fallback_total = 0
    alloc_total = 0

    with derived_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for d in r:
            ra = (d.get("fallback_ratio") or "").strip()
            if ra:
                try:
                    ratios.append(float(ra))
                except ValueError:
                    pass
            a = (d.get("d_anon_fault_alloc") or "").strip()
            fb = (d.get("d_anon_fault_fallback") or "").strip()
            at = (d.get("attempts") or "").strip()
            if a.isdigit():
                alloc_total += int(a)
            if fb.lstrip("-").isdigit():
                fallback_total += int(fb)
            if at.isdigit():
                attempts_total += int(at)

    overall_ratio = (fallback_total / attempts_total) if attempts_total > 0 else None

    lines: List[str] = []
    lines.append("# THP 64KB anon fallback summary\n")
    if overall_ratio is not None:
        lines.append(f"- **overall fallback_ratio**: {overall_ratio:.6f}  ")
        lines.append(f"  (fallback={fallback_total}, attempts={attempts_total}, alloc={alloc_total})\n")
    else:
        lines.append("- overall fallback_ratio: N/A (no valid attempts)\n")

    if ratios:
        lines.append(f"- windows with valid ratio: {len(ratios)}")
        lines.append(f"- ratio median: {statistics.median(ratios):.6f}")
        lines.append(f"- ratio p90: {statistics.quantiles(ratios, n=10)[8]:.6f} (approx)\n")
    else:
        lines.append("- no valid per-window ratios (check raw_samples.csv errors / missing counters)\n")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="derive deltas and fallback ratios from raw_samples.csv")
    p.add_argument("raw_csv", help="Path to raw_samples.csv")
    p.add_argument("--out-dir", default=None, help="Output dir (default: same dir as raw)")

    args = p.parse_args(argv)

    raw_path = Path(args.raw_csv)
    if not raw_path.exists():
        raise FileNotFoundError(str(raw_path))

    out_dir = Path(args.out_dir) if args.out_dir else raw_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = read_raw(raw_path)
    if len(raw) < 2:
        (out_dir / "summary.md").write_text("# THP 64KB anon fallback summary\n\nNot enough samples.\n", encoding="utf-8")
        return 0

    derived_csv = out_dir / "derived.csv"
    write_derived(raw, derived_csv)

    summary_md = out_dir / "summary.md"
    write_summary(derived_csv, summary_md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
