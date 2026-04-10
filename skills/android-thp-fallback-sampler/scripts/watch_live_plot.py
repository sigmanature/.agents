#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Periodically generate "live" derived + SVG plots while a long run is ongoing.

Motivation:
- `run_memstress_and_collect_logs.py` writes `raw_samples.csv` continuously, but `derived.csv` and plots
  are typically produced at the end of the run.
- During 16h+ runs it is useful to view trend updates without stopping the experiment.

This script:
1) snapshots per-device `raw_samples.csv` (trim incomplete last line)
2) runs `derive_metrics.py` on the snapshots
3) runs `plot_derived_svg.py` to generate a comparison SVG (dependency-light)

Outputs (under --plot-dir, default: <out_dir>/live_plot):
- latest/ fallback_ratio.svg + plot_summary.md
- archive/<timestamp>/... (optional)
- status.json (quick state, last update times, line counts)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _run(cmd: Sequence[str], *, timeout_s: int) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout_s)


def snapshot_csv_trim_last_line(src: Path, dst: Path) -> Tuple[int, int]:
    """Copy src -> dst but ensure dst ends at a line boundary.

    Returns (bytes_written, newline_count).
    """

    data = src.read_bytes()
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        trimmed = b""
    else:
        trimmed = data[: last_nl + 1]
    dst.write_bytes(trimmed)
    return (len(trimmed), trimmed.count(b"\n"))


def discover_serials(out_dir: Path) -> List[str]:
    serials: List[str] = []
    if not out_dir.exists():
        return serials
    for child in sorted(out_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "raw_samples.csv").exists():
            serials.append(child.name)
    return serials


@dataclass
class SeriesInfo:
    serial: str
    raw_src: Path
    raw_snap: Path
    derived_dir: Path
    derived_csv: Path
    raw_lines: int = 0
    snap_lines: int = 0
    ok: bool = False
    err: str = ""

    def to_json(self) -> Dict[str, object]:
        def _v(x: object) -> object:
            if isinstance(x, Path):
                return str(x)
            return x

        d = {
            "serial": self.serial,
            "raw_src": self.raw_src,
            "raw_snap": self.raw_snap,
            "derived_dir": self.derived_dir,
            "derived_csv": self.derived_csv,
            "raw_lines": self.raw_lines,
            "snap_lines": self.snap_lines,
            "ok": self.ok,
            "err": self.err,
        }
        return {k: _v(v) for k, v in d.items()}


def build_series(out_dir: Path, plot_tmp: Path, serial: str) -> SeriesInfo:
    raw_src = out_dir / serial / "raw_samples.csv"
    raw_snap = plot_tmp / f"raw_samples_{serial}.csv"
    derived_dir = plot_tmp / f"derived_{serial}"
    derived_csv = derived_dir / "derived.csv"
    return SeriesInfo(
        serial=serial,
        raw_src=raw_src,
        raw_snap=raw_snap,
        derived_dir=derived_dir,
        derived_csv=derived_csv,
    )


def run_once(
    *,
    out_dir: Path,
    plot_dir: Path,
    serials: Sequence[str],
    align: str,
    title: str,
    archive: bool,
    timeout_s: int,
) -> Dict[str, object]:
    scripts_dir = Path(__file__).resolve().parent
    derive = scripts_dir / "derive_metrics.py"
    plot = scripts_dir / "plot_derived_svg.py"

    plot_tmp = plot_dir / "tmp"
    plot_latest = plot_dir / "latest"
    plot_archive_root = plot_dir / "archive"
    _mkdir(plot_tmp)
    _mkdir(plot_latest)
    if archive:
        _mkdir(plot_archive_root)

    ts = _now_ts()

    series_list: List[SeriesInfo] = [build_series(out_dir, plot_tmp, s) for s in serials]

    # Snapshot + derive
    derived_paths: List[Path] = []
    labels: List[str] = []
    for info in series_list:
        try:
            if not info.raw_src.exists():
                info.err = f"missing raw: {info.raw_src}"
                continue

            # Best-effort line count for status.
            try:
                info.raw_lines = max(0, len(info.raw_src.read_text(encoding="utf-8", errors="ignore").splitlines()))
            except Exception:
                info.raw_lines = 0

            _mkdir(info.derived_dir)
            _, nl = snapshot_csv_trim_last_line(info.raw_src, info.raw_snap)
            info.snap_lines = max(0, nl)

            cp = _run(
                [sys.executable, str(derive), str(info.raw_snap), "--out-dir", str(info.derived_dir)],
                timeout_s=timeout_s,
            )
            if cp.returncode != 0:
                info.err = (cp.stderr or cp.stdout or f"derive rc={cp.returncode}").strip()[:400]
                continue

            if not info.derived_csv.exists():
                info.err = "derived.csv not produced yet (need >=2 samples)"
                continue

            derived_paths.append(info.derived_csv)
            labels.append(info.serial)
            info.ok = True
        except Exception as e:
            info.err = str(e)[:400]

    if len(derived_paths) < 1:
        return {
            "ok": False,
            "ts": ts,
            "error": "no derived.csv available yet",
            "series": [info.to_json() for info in series_list],
        }

    # Plot into latest/
    cmd: List[str] = [
        sys.executable,
        str(plot),
        *[str(p) for p in derived_paths],
        "--out-dir",
        str(plot_latest),
        "--align",
        str(align),
        "--title",
        str(title),
    ]
    for lab in labels:
        cmd.extend(["--label", lab])

    cp_plot = _run(cmd, timeout_s=timeout_s)
    if cp_plot.returncode != 0:
        err = (cp_plot.stderr or cp_plot.stdout or f"plot rc={cp_plot.returncode}").strip()[:400]
        return {
            "ok": False,
            "ts": ts,
            "error": err,
            "series": [info.to_json() for info in series_list],
        }

    archived_dir: Optional[str] = None
    if archive:
        dst = plot_archive_root / ts
        _mkdir(dst)
        for name in ("fallback_ratio.svg", "plot_summary.md"):
            src = plot_latest / name
            if src.exists():
                shutil.copy2(src, dst / name)
        for info in series_list:
            if info.ok and info.derived_csv.exists():
                shutil.copy2(info.derived_csv, dst / f"derived_{info.serial}.csv")
        archived_dir = str(dst)

    return {
        "ok": True,
        "ts": ts,
        "archived_dir": archived_dir,
        "plot_latest_dir": str(plot_latest),
        "series": [info.to_json() for info in series_list],
        "plot_stdout_tail": (cp_plot.stdout or "").strip()[-400:],
        "plot_stderr_tail": (cp_plot.stderr or "").strip()[-400:],
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Periodically generate derived+SVG plots during a long run")
    p.add_argument("--out-dir", required=True, help="Run output dir (contains <serial>/raw_samples.csv)")
    p.add_argument("--serial", action="append", default=[], help="Device serial (repeatable). If omitted, auto-discover from out-dir")
    p.add_argument("--plot-dir", default=None, help="Plot output dir (default: <out_dir>/live_plot)")
    p.add_argument("--every-s", type=int, default=1800, help="Regenerate every N seconds (default: 1800)")
    p.add_argument("--align", choices=["relative", "absolute"], default="absolute", help="X-axis alignment (default: absolute)")
    p.add_argument("--title", default="THP anon fallback_ratio (live)", help="Plot title")
    p.add_argument("--no-archive", action="store_true", help="Do not create archive/<ts>/ snapshots")
    p.add_argument("--timeout-s", type=int, default=300, help="Timeout seconds for derive/plot steps (default: 300)")
    p.add_argument("--once", action="store_true", help="Run once and exit")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    if not out_dir.exists():
        raise FileNotFoundError(str(out_dir))

    plot_dir = Path(args.plot_dir).resolve() if args.plot_dir else (out_dir / "live_plot")
    _mkdir(plot_dir)

    serials = list(args.serial or [])
    if not serials:
        serials = discover_serials(out_dir)
    if not serials:
        raise RuntimeError(f"no serials discovered under out_dir: {out_dir}")

    status_path = plot_dir / "status.json"
    print(f"[watch] out_dir={out_dir}")
    print(f"[watch] plot_dir={plot_dir}")
    print(f"[watch] serials={','.join(serials)} every_s={int(args.every_s)} align={args.align}")

    while True:
        res = run_once(
            out_dir=out_dir,
            plot_dir=plot_dir,
            serials=serials,
            align=str(args.align),
            title=str(args.title),
            archive=(not args.no_archive),
            timeout_s=max(30, int(args.timeout_s)),
        )
        status_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if res.get("ok"):
            print(f"[watch] {res.get('ts')} ok -> {plot_dir/'latest'/'fallback_ratio.svg'}")
        else:
            print(f"[watch] {res.get('ts')} not-ready: {res.get('error')}")

        if args.once:
            break
        time.sleep(max(1, int(args.every_s)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
