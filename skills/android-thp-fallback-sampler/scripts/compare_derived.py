#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two derived.csv runs and (optionally) plot fallback_ratio trends.

This script is host-side and reads outputs from this skill, typically:
- <out_dir>/derived.csv

Outputs:
- compare_summary.md
- compare.png (only if matplotlib is available)
"""

from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


# === CONFIG (edit here) ===
CONFIG = {
    "png_filename": "compare.png",
    "summary_filename": "compare_summary.md",
    "max_points": 5000,  # avoid accidental huge plots
}


@dataclass
class Series:
    label: str
    t_min: List[float]
    ratio: List[float]
    attempts: int
    fallback: int


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    if s.lstrip("-").isdigit():
        return int(s)
    return None


def _to_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_derived(path: Path, label: str) -> Series:
    t: List[float] = []
    ratio: List[float] = []
    attempts_total = 0
    fallback_total = 0

    first_ts: Optional[int] = None

    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            if i >= int(CONFIG["max_points"]):
                break

            host_ts = _to_int(row.get("window_end_host_ts", ""))
            ra = _to_float(row.get("fallback_ratio", ""))
            at = _to_int(row.get("attempts", "")) or 0
            fb = _to_int(row.get("d_anon_fault_fallback", "")) or 0

            if host_ts is not None and first_ts is None:
                first_ts = host_ts

            if ra is None:
                continue

            if host_ts is None or first_ts is None:
                # Fallback: use index as x-axis.
                t.append(float(len(t)))
            else:
                t.append((host_ts - first_ts) / 60.0)
            ratio.append(ra)
            attempts_total += max(0, at)
            fallback_total += max(0, fb)

    return Series(label=label, t_min=t, ratio=ratio, attempts=attempts_total, fallback=fallback_total)


def try_plot(a: Series, b: Series, out_png: Path) -> bool:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return False

    plt.figure(figsize=(12, 5))
    if a.t_min and a.ratio:
        plt.plot(a.t_min, a.ratio, label=a.label, linewidth=1)
    if b.t_min and b.ratio:
        plt.plot(b.t_min, b.ratio, label=b.label, linewidth=1)
    plt.xlabel("time (min since first sample, or index)")
    plt.ylabel("fallback_ratio")
    plt.title("THP anon fallback_ratio comparison")
    plt.grid(True, alpha=0.2)
    plt.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_png)
    plt.close()
    return True


def write_summary(a: Series, b: Series, out_md: Path, plot_ok: bool) -> None:
    def _overall_ratio(s: Series) -> Optional[float]:
        return (s.fallback / s.attempts) if s.attempts > 0 else None

    lines: List[str] = []
    lines.append("# THP fallback_ratio compare\n")
    lines.append(f"- A: `{a.label}`")
    lines.append(f"- B: `{b.label}`\n")

    for s in (a, b):
        overall = _overall_ratio(s)
        lines.append(f"## {s.label}\n")
        if overall is None:
            lines.append("- overall fallback_ratio: N/A (no valid attempts)\n")
        else:
            lines.append(f"- overall fallback_ratio: {overall:.6f}  ")
            lines.append(f"  (fallback={s.fallback}, attempts={s.attempts})\n")
        if s.ratio:
            lines.append(f"- points: {len(s.ratio)}")
            lines.append(f"- ratio median: {statistics.median(s.ratio):.6f}")
            lines.append(f"- ratio p90 (approx): {statistics.quantiles(s.ratio, n=10)[8]:.6f}\n")
        else:
            lines.append("- no ratio points (check derived.csv)\n")

    if plot_ok:
        lines.append(f"- plot: `{out_md.parent / CONFIG['png_filename']}`\n")
    else:
        lines.append("- plot: skipped (matplotlib not available)\n")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare two derived.csv runs (and plot if possible)")
    p.add_argument("derived_a", help="Path to run A derived.csv")
    p.add_argument("derived_b", help="Path to run B derived.csv")
    p.add_argument("--label-a", default="A", help="Legend label for A")
    p.add_argument("--label-b", default="B", help="Legend label for B")
    p.add_argument("--out-dir", default=None, help="Output directory (default: ./output/compare_<timestamp>)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    a_path = Path(args.derived_a)
    b_path = Path(args.derived_b)
    if not a_path.exists():
        raise FileNotFoundError(str(a_path))
    if not b_path.exists():
        raise FileNotFoundError(str(b_path))

    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / f"compare_{_now_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    a = load_derived(a_path, args.label_a)
    b = load_derived(b_path, args.label_b)

    png_path = out_dir / str(CONFIG["png_filename"])
    plot_ok = try_plot(a, b, png_path)
    write_summary(a, b, out_dir / str(CONFIG["summary_filename"]), plot_ok=plot_ok)

    print(f"Done. out_dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

