#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class SeriesSummary:
    label: str
    run_dir: str
    windows: int
    overall_ratio: float
    median_ratio: float
    p90_ratio: float
    last10_avg: float
    last60_avg: float
    last10_max: float
    valid_end_ts: int


def _read_summary(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- **overall fallback_ratio**:"):
            out["overall_ratio"] = float(line.split(":")[1].split()[0])
        elif line.startswith("- ratio median:"):
            out["median_ratio"] = float(line.split(":")[1].strip())
        elif line.startswith("- ratio p90:"):
            out["p90_ratio"] = float(line.split(":")[1].split()[0])
        elif line.startswith("- windows with valid ratio:"):
            out["windows"] = int(line.split(":")[1].strip())
    return out


def _read_derived(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_float(x: str) -> Optional[float]:
    x = (x or "").strip()
    if not x:
        return None
    return float(x)


def _moving_avg(vals: List[float], window: int) -> List[float]:
    out: List[float] = []
    for i in range(len(vals)):
        s = vals[max(0, i - window + 1): i + 1]
        out.append(sum(s) / len(s))
    return out


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def write_line_chart_svg(
    out_path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    series: List[tuple[str, List[float], str]],
) -> None:
    width = 960
    height = 520
    left = 80
    right = 30
    top = 50
    bottom = 60
    plot_w = width - left - right
    plot_h = height - top - bottom

    all_vals = [v for _, vals, _ in series for v in vals]
    y_max = max(all_vals) if all_vals else 1.0
    y_max = max(y_max, 1e-9)
    x_max = max((len(vals) for _, vals, _ in series), default=1)
    x_max = max(x_max, 1)

    def map_x(i: int, n: int) -> float:
        if n <= 1:
            return left
        return left + (i / (n - 1)) * plot_w

    def map_y(v: float) -> float:
        return top + plot_h - (v / y_max) * plot_h

    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    parts.append(f'<text x="{width/2:.1f}" y="24" text-anchor="middle" font-size="18" font-family="sans-serif">{_svg_escape(title)}</text>')

    # axes
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333" stroke-width="1.5"/>')
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333" stroke-width="1.5"/>')

    # y grid and labels
    for idx in range(6):
        frac = idx / 5
        y = top + plot_h - frac * plot_h
        val = frac * y_max
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-size="11" font-family="sans-serif" fill="#555">{val:.3f}</text>')

    # x labels
    for idx in range(6):
        frac = idx / 5
        x = left + frac * plot_w
        val = int(round(frac * x_max))
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" stroke="#f3f4f6" stroke-width="1"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + plot_h + 22}" text-anchor="middle" font-size="11" font-family="sans-serif" fill="#555">{val}</text>')

    for label, vals, color in series:
        if not vals:
            continue
        pts = " ".join(f"{map_x(i, len(vals)):.2f},{map_y(v):.2f}" for i, v in enumerate(vals))
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{pts}"/>')

    # axis labels
    parts.append(f'<text x="{width/2:.1f}" y="{height - 15}" text-anchor="middle" font-size="13" font-family="sans-serif">{_svg_escape(xlabel)}</text>')
    parts.append(
        f'<text x="22" y="{height/2:.1f}" text-anchor="middle" font-size="13" font-family="sans-serif" transform="rotate(-90 22,{height/2:.1f})">{_svg_escape(ylabel)}</text>'
    )

    # legend
    legend_x = left + 10
    legend_y = top + 10
    for idx, (label, _, color) in enumerate(series):
        y = legend_y + idx * 20
        parts.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 20}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{legend_x + 28}" y="{y + 4}" font-size="12" font-family="sans-serif">{_svg_escape(label)}</text>')

    parts.append("</svg>")
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_summary(label: str, run_dir: Path) -> tuple[SeriesSummary, List[int], List[float], List[float]]:
    summary = _read_summary(run_dir / "summary.md")
    rows = _read_derived(run_dir / "derived.csv")
    ts = [int(r["window_end_host_ts"]) for r in rows if r.get("fallback_ratio")]
    vals = [_to_float(r["fallback_ratio"]) for r in rows]
    vals = [v for v in vals if v is not None]
    cumulative = []
    total_fallback = 0
    total_attempts = 0
    for r in rows:
        fb = int(r["d_anon_fault_fallback"]) if (r.get("d_anon_fault_fallback") or "").isdigit() else 0
        at = int(r["attempts"]) if (r.get("attempts") or "").isdigit() else 0
        total_fallback += fb
        total_attempts += at
        cumulative.append((total_fallback / total_attempts) if total_attempts else 0.0)
    last10 = vals[-10:] if vals else []
    last60 = vals[-60:] if vals else []
    meta = SeriesSummary(
        label=label,
        run_dir=str(run_dir),
        windows=summary["windows"],
        overall_ratio=summary["overall_ratio"],
        median_ratio=summary["median_ratio"],
        p90_ratio=summary["p90_ratio"],
        last10_avg=(sum(last10) / len(last10)) if last10 else 0.0,
        last60_avg=(sum(last60) / len(last60)) if last60 else 0.0,
        last10_max=max(last10) if last10 else 0.0,
        valid_end_ts=ts[-1] if ts else 0,
    )
    return meta, ts, vals, cumulative


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot dual THP fallback report assets.")
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--folio-dir", required=True)
    parser.add_argument("--baseline-label", default="BASE_S")
    parser.add_argument("--folio-label", default="FOLIO_S")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    b_meta, b_ts, b_vals, b_cum = build_summary(args.baseline_label, Path(args.baseline_dir))
    f_meta, f_ts, f_vals, f_cum = build_summary(args.folio_label, Path(args.folio_dir))

    ratio_svg = out_dir / "window_ratio_trend.svg"
    write_line_chart_svg(
        ratio_svg,
        title="Window Fallback Ratio Trend",
        xlabel="Sampling Window",
        ylabel="Fallback Ratio",
        series=[
            (f"{args.baseline_label} rolling10", _moving_avg(b_vals, 10), "#1f77b4"),
            (f"{args.folio_label} rolling10", _moving_avg(f_vals, 10), "#d62728"),
        ],
    )

    cum_svg = out_dir / "cumulative_ratio_trend.svg"
    write_line_chart_svg(
        cum_svg,
        title="Cumulative Fallback Ratio",
        xlabel="Sampling Window",
        ylabel="Cumulative Fallback Ratio",
        series=[
            (f"{args.baseline_label} cumulative", b_cum, "#1f77b4"),
            (f"{args.folio_label} cumulative", f_cum, "#d62728"),
        ],
    )

    metrics = {
        "baseline": b_meta.__dict__,
        "folio": f_meta.__dict__,
        "delta_overall_ratio": f_meta.overall_ratio - b_meta.overall_ratio,
        "relative_improvement_vs_baseline": (
            (b_meta.overall_ratio - f_meta.overall_ratio) / b_meta.overall_ratio
            if b_meta.overall_ratio else 0.0
        ),
        "figures": {
            "window_ratio_trend": str(ratio_svg),
            "cumulative_ratio_trend": str(cum_svg),
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
