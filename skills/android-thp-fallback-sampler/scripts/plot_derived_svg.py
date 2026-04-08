#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plot one or more derived.csv series as dependency-light SVG.

Why:
- Many hosts (including CI / minimal lab machines) do not have matplotlib/pandas.
- The core output we care about is fallback_ratio trend over time.

Inputs:
- One or more derived.csv files produced by this skill.

Outputs (under --out-dir):
- fallback_ratio.svg
- plot_summary.md
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


CONFIG = {
    "svg_width": 1200,
    "svg_height": 520,
    "pad_left": 70,
    "pad_right": 20,
    "pad_top": 40,
    "pad_bottom": 70,
    "max_points": 8000,  # hard cap for safety
    "default_ticks": 5,
}


PALETTE = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _to_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


@dataclass
class Series:
    label: str
    host_ts: List[int]
    ratio: List[float]
    attempts: List[int]

    def stats(self) -> Dict[str, object]:
        r = [x for x in self.ratio if x is not None and math.isfinite(x)]
        if not r:
            return {"points": 0}
        out: Dict[str, object] = {
            "points": len(r),
            "min": min(r),
            "max": max(r),
            "median": statistics.median(r),
            "p90_approx": statistics.quantiles(r, n=10)[8] if len(r) >= 10 else None,
        }
        return out


def load_derived(path: Path, label: str, *, max_points: int) -> Series:
    host_ts: List[int] = []
    ratio: List[float] = []
    attempts: List[int] = []

    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            if i >= max_points:
                break
            ts = _to_int(row.get("window_end_host_ts", ""))
            ra = _to_float(row.get("fallback_ratio", ""))
            at = _to_int(row.get("attempts", "")) or 0
            if ts is None or ra is None:
                continue
            host_ts.append(ts)
            ratio.append(ra)
            attempts.append(max(0, at))

    return Series(label=label, host_ts=host_ts, ratio=ratio, attempts=attempts)


def downsample(series: Series, *, max_points: int) -> Series:
    n = len(series.host_ts)
    if n <= max_points:
        return series
    step = max(1, int(math.ceil(n / max_points)))
    idx = list(range(0, n, step))
    return Series(
        label=series.label,
        host_ts=[series.host_ts[i] for i in idx],
        ratio=[series.ratio[i] for i in idx],
        attempts=[series.attempts[i] for i in idx],
    )


def _nice_ticks(lo: float, hi: float, n: int) -> List[float]:
    if n <= 1:
        return [lo]
    if hi <= lo:
        return [lo for _ in range(n)]
    return [lo + (hi - lo) * (i / (n - 1)) for i in range(n)]


def _svg_text(x: float, y: float, text: str, *, size: int = 12, anchor: str = "start") -> str:
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}">{safe}</text>'


def plot_fallback_ratio_svg(
    series_list: Sequence[Series],
    *,
    out_svg: Path,
    title: str,
    align: str,
    ticks: int,
) -> None:
    w = int(CONFIG["svg_width"])
    h = int(CONFIG["svg_height"])
    pl = int(CONFIG["pad_left"])
    pr = int(CONFIG["pad_right"])
    pt = int(CONFIG["pad_top"])
    pb = int(CONFIG["pad_bottom"])

    # Build x as minutes.
    all_x: List[float] = []
    all_y: List[float] = []
    x_by_series: List[List[float]] = []
    for s in series_list:
        if not s.host_ts or not s.ratio:
            x_by_series.append([])
            continue

        if align == "absolute":
            # Normalize to the earliest sample among all series.
            # We'll compute after collecting min_ts.
            x_by_series.append([float(ts) for ts in s.host_ts])
        else:
            t0 = s.host_ts[0]
            x_by_series.append([(ts - t0) / 60.0 for ts in s.host_ts])

        all_y.extend([_clamp(y, 0.0, 1.0) for y in s.ratio])

    if align == "absolute":
        # Convert host_ts to minutes since min(all series).
        min_ts = min((min(s.host_ts) for s in series_list if s.host_ts), default=0)
        x_by_series2: List[List[float]] = []
        for xs in x_by_series:
            x_by_series2.append([(v - min_ts) / 60.0 for v in xs])
        x_by_series = x_by_series2

    for xs in x_by_series:
        all_x.extend(xs)

    if not all_x or not all_y:
        out_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>\n", encoding="utf-8")
        return

    x_min = min(all_x)
    x_max = max(all_x)
    y_min = 0.0
    y_max = max(0.02, max(all_y))
    y_max = min(1.0, y_max * 1.05)

    plot_w = max(10, w - pl - pr)
    plot_h = max(10, h - pt - pb)

    def sx(x: float) -> float:
        return pl + plot_w * _safe_div(x - x_min, x_max - x_min)

    def sy(y: float) -> float:
        return pt + plot_h * (1.0 - _safe_div(y - y_min, y_max - y_min))

    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">')
    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="white"/>')

    # Title
    parts.append(_svg_text(pl, 24, title, size=16, anchor="start"))
    subtitle = f"align={align}  series={len(series_list)}"
    parts.append(_svg_text(w - pr, 24, subtitle, size=12, anchor="end"))

    # Axes
    x0 = pl
    y0 = pt + plot_h
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{pl + plot_w}" y2="{y0}" stroke="#333" stroke-width="1"/>')
    parts.append(f'<line x1="{x0}" y1="{pt}" x2="{x0}" y2="{y0}" stroke="#333" stroke-width="1"/>')

    # Grid + ticks
    for v in _nice_ticks(x_min, x_max, max(2, ticks)):
        x = sx(v)
        parts.append(f'<line x1="{x:.2f}" y1="{pt}" x2="{x:.2f}" y2="{y0}" stroke="#eee" stroke-width="1"/>')
        parts.append(_svg_text(x, y0 + 18, f"{v:.1f}", size=11, anchor="middle"))
    parts.append(_svg_text(pl + plot_w / 2, h - 18, "time (min)", size=12, anchor="middle"))

    for v in _nice_ticks(y_min, y_max, max(2, ticks)):
        y = sy(v)
        parts.append(f'<line x1="{x0}" y1="{y:.2f}" x2="{pl + plot_w}" y2="{y:.2f}" stroke="#eee" stroke-width="1"/>')
        parts.append(_svg_text(x0 - 10, y + 4, f"{v:.3f}", size=11, anchor="end"))
    parts.append(_svg_text(18, pt + plot_h / 2, "fallback_ratio", size=12, anchor="middle"))

    # Series polylines + legend
    legend_x = pl
    legend_y = pt + plot_h + 44
    for i, s in enumerate(series_list):
        xs = x_by_series[i]
        if not xs or not s.ratio:
            continue
        color = PALETTE[i % len(PALETTE)]
        pts: List[str] = []
        for x, y in zip(xs, s.ratio):
            pts.append(f"{sx(x):.2f},{sy(_clamp(y, 0.0, 1.0)):.2f}")
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.2" points="{" ".join(pts)}"/>')

        # Legend entry
        lx = legend_x + i * 220
        parts.append(f'<rect x="{lx:.2f}" y="{legend_y - 10:.2f}" width="14" height="4" fill="{color}"/>')
        parts.append(_svg_text(lx + 20, legend_y - 6, s.label, size=12, anchor="start"))

    parts.append("</svg>\n")
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(parts), encoding="utf-8")


def write_summary(series_list: Sequence[Series], out_md: Path, *, svg_path: Path) -> None:
    lines: List[str] = []
    lines.append("# THP derived.csv plot summary\n")
    lines.append(f"- svg: `{svg_path}`\n")
    for s in series_list:
        st = s.stats()
        lines.append(f"## {s.label}\n")
        lines.append(f"- points: {st.get('points', 0)}")
        if st.get("points", 0):
            lines.append(f"- ratio min/max: {st['min']:.6f} / {st['max']:.6f}")
            lines.append(f"- ratio median: {st['median']:.6f}")
            if st.get("p90_approx") is not None:
                lines.append(f"- ratio p90 (approx): {float(st['p90_approx']):.6f}")
        lines.append("")
    out_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot derived.csv files into SVG (no matplotlib dependency)")
    p.add_argument("derived", nargs="+", help="Path(s) to derived.csv")
    p.add_argument("--label", action="append", default=[], help="Series label(s), repeatable (default: infer from path)")
    p.add_argument("--out-dir", default=None, help="Output directory (default: output/plot_<timestamp>)")
    p.add_argument(
        "--align",
        choices=["relative", "absolute"],
        default="relative",
        help="relative: x=minutes since each series first sample; absolute: x=minutes since earliest sample across series",
    )
    p.add_argument("--ticks", type=int, default=int(CONFIG["default_ticks"]), help="Number of axis ticks (approx)")
    p.add_argument("--title", default="THP anon fallback_ratio", help="Plot title")
    p.add_argument("--max-points", type=int, default=int(CONFIG["max_points"]), help="Max points per series after load")
    return p.parse_args(argv)


def infer_label(path: Path) -> str:
    # Prefer serial-ish directory name if present.
    parts = [p for p in path.parts if p]
    for cand in reversed(parts):
        if cand.lower() in ("derived.csv",):
            continue
        if len(cand) >= 8:
            return cand
    return path.parent.name or "series"


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    paths = [Path(p) for p in args.derived]
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(str(p))

    labels = list(args.label or [])
    while len(labels) < len(paths):
        labels.append(infer_label(paths[len(labels)]))
    labels = labels[: len(paths)]

    out_dir = Path(args.out_dir) if args.out_dir else Path("output") / f"plot_{_now_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    series_list: List[Series] = []
    for p, lab in zip(paths, labels):
        s = load_derived(p, lab, max_points=min(int(args.max_points), int(CONFIG["max_points"])))
        s = downsample(s, max_points=min(int(args.max_points), int(CONFIG["max_points"])))
        series_list.append(s)

    svg_path = out_dir / "fallback_ratio.svg"
    plot_fallback_ratio_svg(
        series_list,
        out_svg=svg_path,
        title=str(args.title),
        align=str(args.align),
        ticks=max(2, int(args.ticks)),
    )
    write_summary(series_list, out_dir / "plot_summary.md", svg_path=svg_path)

    print(f"Done. out_dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
