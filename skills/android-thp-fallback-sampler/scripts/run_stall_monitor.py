#!/usr/bin/env python3
"""Periodic direct reclaim / compaction monitor with vmstat sampling + stall trace snapshots.

Runs two concurrent loops:
1. vmstat sampler: reads /proc/vmstat counters every --sample-interval-s seconds
2. stall trace: captures a --trace-duration-s ftrace snapshot every --trace-interval-s seconds

Outputs:
    vmstat_samples.csv          - cumulative counters per sample tick
    vmstat_derived.csv          - per-interval deltas
    stall_snapshots/            - one subdirectory per trace snapshot
    summary.json                - run metadata
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.adb_utils import adb_shell_cp, ensure_adb_works, resolve_serial
from trace_highorder_stalls import cleanup_trace, read_trace, parse_trace, write_outputs


VMSTAT_KEYS = [
    "allocstall_normal",
    "allocstall_movable",
    "pgscan_direct",
    "pgsteal_direct",
    "pgscan_kswapd",
    "pgsteal_kswapd",
    "pgscan_direct_throttle",
    "compact_stall",
    "compact_success",
    "pageoutrun",
    "kswapd_inodesteal",
]

TRACE_BASE = "/sys/kernel/tracing"
TRACE_EVENTS = [
    f"{TRACE_BASE}/events/kmem/mm_page_alloc",
    f"{TRACE_BASE}/events/vmscan/mm_vmscan_direct_reclaim_begin",
    f"{TRACE_BASE}/events/vmscan/mm_vmscan_direct_reclaim_end",
    f"{TRACE_BASE}/events/compaction/mm_compaction_try_to_compact_pages",
    f"{TRACE_BASE}/events/compaction/mm_compaction_begin",
    f"{TRACE_BASE}/events/compaction/mm_compaction_end",
]


def _su_write(serial: str, path: str, value: str, *, check: bool = True) -> bool:
    cp = adb_shell_cp(serial, f"su -c \"printf '%s' '{value}' > {path}\"", timeout_s=10, check=False)
    if cp.returncode != 0:
        if check:
            raise RuntimeError(f"failed to write {path}: {cp.stderr}")
        return False
    return True


def configure_trace_lenient(serial: str, *, min_order: int, buffer_kb: int) -> List[str]:
    _su_write(serial, f"{TRACE_BASE}/tracing_on", "0")
    for ev in TRACE_EVENTS:
        _su_write(serial, f"{ev}/enable", "0", check=False)
    _su_write(serial, f"{TRACE_EVENTS[0]}/filter", f"order >= {min_order}", check=False)
    _su_write(serial, f"{TRACE_BASE}/options/stacktrace", "1")
    _su_write(serial, f"{TRACE_BASE}/buffer_size_kb", str(buffer_kb))
    _su_write(serial, f"{TRACE_BASE}/trace", "")
    enabled = []
    for ev in TRACE_EVENTS:
        if _su_write(serial, f"{ev}/enable", "1", check=False):
            enabled.append(ev.split("/")[-2] + "/" + ev.split("/")[-1])
    return enabled


def read_vmstat(serial: str) -> Dict[str, int]:
    cp = adb_shell_cp(serial, "su -c 'cat /proc/vmstat'", timeout_s=15, check=False)
    out: Dict[str, int] = {}
    for line in (cp.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in VMSTAT_KEYS:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def vmstat_sample_loop(
    serial: str,
    out_csv: Path,
    interval_s: int,
    stop: threading.Event,
) -> int:
    fieldnames = ["host_ts"] + VMSTAT_KEYS
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        next_t = time.time()
        while not stop.is_set():
            now = time.time()
            if now < next_t:
                stop.wait(min(next_t - now, 1.0))
                continue
            values = read_vmstat(serial)
            row = {"host_ts": int(time.time())}
            for k in VMSTAT_KEYS:
                row[k] = values.get(k, 0)
            w.writerow(row)
            f.flush()
            count += 1
            next_t += interval_s
    return count


def derive_vmstat(raw_csv: Path, out_csv: Path) -> None:
    rows = []
    with raw_csv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if len(rows) < 2:
        return

    fieldnames = ["host_ts", "dt_s"] + [f"d_{k}" for k in VMSTAT_KEYS]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i in range(1, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            dt = int(cur["host_ts"]) - int(prev["host_ts"])
            derived = {"host_ts": cur["host_ts"], "dt_s": dt}
            for k in VMSTAT_KEYS:
                derived[f"d_{k}"] = int(cur.get(k, 0)) - int(prev.get(k, 0))
            w.writerow(derived)


def stall_trace_loop(
    serial: str,
    out_dir: Path,
    *,
    trace_duration_s: int,
    trace_interval_s: int,
    min_order: int,
    buffer_kb: int,
    stop: threading.Event,
) -> int:
    snapshots_dir = out_dir / "stall_snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    while not stop.is_set():
        ts = time.strftime("%Y%m%d_%H%M%S")
        snap_dir = snapshots_dir / f"snap_{ts}"

        try:
            enabled = configure_trace_lenient(serial, min_order=min_order, buffer_kb=buffer_kb)
            adb_shell_cp(serial, f"su -c 'printf \"%s\" \"1\" > {TRACE_BASE}/tracing_on'", timeout_s=10)

            deadline = time.time() + trace_duration_s
            while time.time() < deadline and not stop.is_set():
                time.sleep(0.5)

            adb_shell_cp(serial, f"su -c 'printf \"%s\" \"0\" > {TRACE_BASE}/tracing_on'", timeout_s=10)
            raw = read_trace(serial)
            report = parse_trace(raw)
            write_outputs(snap_dir, raw, report)
            count += 1
            print(
                f"[stall_trace] snap={ts} events={len(report.events)} "
                f"stalls={len(report.stalls)}"
            )
        except Exception as e:
            print(f"[stall_trace] snap={ts} ERROR: {e}", file=sys.stderr)
        finally:
            try:
                cleanup_trace(serial)
            except Exception:
                pass

        wait_s = max(0, trace_interval_s - trace_duration_s)
        if wait_s > 0 and not stop.is_set():
            stop.wait(wait_s)

    return count


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Periodic stall monitor: vmstat sampling + ftrace snapshots")
    p.add_argument("--serial", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--duration-s", type=int, default=0, help="Total run duration (0=run until stopped)")
    p.add_argument("--sample-interval-s", type=int, default=60, help="vmstat sampling interval")
    p.add_argument("--trace-duration-s", type=int, default=30, help="Each stall trace snapshot duration")
    p.add_argument("--trace-interval-s", type=int, default=120, help="Time between stall trace snapshots")
    p.add_argument("--min-order", type=int, default=2)
    p.add_argument("--buffer-kb", type=int, default=65536)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()
    serial = resolve_serial(args.serial)

    out_dir = Path(args.out_dir or f"stall_monitor_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    stop = threading.Event()

    def _handle(_sig, _frame):
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    manifest = {
        "serial": serial,
        "start_ts": int(time.time()),
        "args": vars(args),
    }
    (out_dir / "summary.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    vmstat_csv = out_dir / "vmstat_samples.csv"

    vmstat_thread = threading.Thread(
        target=vmstat_sample_loop,
        args=(serial, vmstat_csv, args.sample_interval_s, stop),
        daemon=True,
    )
    trace_thread = threading.Thread(
        target=stall_trace_loop,
        args=(serial, out_dir),
        kwargs={
            "trace_duration_s": args.trace_duration_s,
            "trace_interval_s": args.trace_interval_s,
            "min_order": args.min_order,
            "buffer_kb": args.buffer_kb,
            "stop": stop,
        },
        daemon=True,
    )

    vmstat_thread.start()
    trace_thread.start()
    print(f"[stall_monitor] started serial={serial} out_dir={out_dir}")

    if args.duration_s > 0:
        stop.wait(args.duration_s)
        stop.set()
    else:
        try:
            while not stop.is_set():
                stop.wait(1.0)
        except KeyboardInterrupt:
            stop.set()

    vmstat_thread.join(timeout=15)
    trace_thread.join(timeout=max(15, args.trace_duration_s + 5))

    derive_vmstat(vmstat_csv, out_dir / "vmstat_derived.csv")

    manifest["end_ts"] = int(time.time())
    manifest["vmstat_samples"] = sum(1 for _ in open(vmstat_csv)) - 1 if vmstat_csv.exists() else 0
    (out_dir / "summary.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[stall_monitor] done. results in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
