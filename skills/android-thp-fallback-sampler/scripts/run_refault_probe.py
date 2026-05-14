#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
MEMSTRESS_SCRIPT = SCRIPT_DIR / "run_memstress_and_collect_logs.py"
SUMMARY_SCRIPT = SCRIPT_DIR / "summarize_refault_probe.py"
TRACE_CAPTURE_SCRIPT = Path("/home/nzzhao/.agents/skills/mmap-lock-contention-analysis/scripts/capture_trace_pipe.py")
TRACE_SETUP_SCRIPT = Path("/home/nzzhao/.agents/skills/mmap-lock-contention-analysis/scripts/setup_tracepoints.sh")


def adb_shell(serial: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", serial, "shell", cmd],
        capture_output=True,
        text=True,
        check=False,
    )


def write_trace_marker(serial: str, message: str) -> subprocess.CompletedProcess:
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    cmd = f"su -c \"sh -c 'echo \\\"{safe}\\\" > /sys/kernel/debug/tracing/trace_marker'\""
    return adb_shell(serial, cmd)


class DropCachesSidecar(threading.Thread):
    def __init__(self, *, serial: str, interval_s: float, log_path: Path, stop_event: threading.Event):
        super().__init__(name=f"drop_caches_{serial}", daemon=True)
        self.serial = serial
        self.interval_s = max(1.0, float(interval_s))
        self.log_path = log_path
        self.stop_event = stop_event
        self.count = 0

    def run(self) -> None:
        with self.log_path.open("a", encoding="utf-8") as logf:
            while not self.stop_event.wait(self.interval_s):
                self.count += 1
                write_trace_marker(self.serial, f"probe:drop_caches:begin count={self.count}")
                cp = adb_shell(self.serial, "su -c 'echo 3 > /proc/sys/vm/drop_caches'")
                write_trace_marker(
                    self.serial,
                    f"probe:drop_caches:end count={self.count} rc={cp.returncode}",
                )
                row = {
                    "host_ts": int(time.time()),
                    "host_ts_sec": round(time.time(), 6),
                    "count": self.count,
                    "returncode": cp.returncode,
                    "stdout_tail": (cp.stdout or "").strip()[-200:],
                    "stderr_tail": (cp.stderr or "").strip()[-200:],
                }
                logf.write(json.dumps(row, ensure_ascii=False) + "\n")
                logf.flush()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a short refault probe with memstress, trace capture, drop_caches, and background summary")
    p.add_argument("--serial", default=os.environ.get("FOLIO_2", ""), help="adb serial; defaults to $FOLIO_2 if set")
    p.add_argument("--package-file", required=True, help="Churn package list file")
    p.add_argument("--victim-package", action="append", default=None, help="Victim package(s) to prime and revisit (repeatable)")
    p.add_argument("--heavy-package-file", default=None, help="Optional heavy package list file")
    p.add_argument("--out-dir", default=None, help="Output directory")
    p.add_argument("--max-cycles", type=int, default=120, help="How many churn cycles to run")
    p.add_argument("--interval-s", type=int, default=60, help="Sampling interval passed to memstress runner")
    p.add_argument("--burst-size", type=int, default=8, help="Churn burst size")
    p.add_argument("--heavy-per-burst", type=int, default=4, help="Preferred heavy apps per burst")
    p.add_argument("--hold-ms", type=int, default=30, help="Per-churn-app foreground dwell")
    p.add_argument("--launch-gap-ms", type=int, default=0, help="Gap between churn launches")
    p.add_argument("--cycle-sleep-ms", type=int, default=0, help="Gap between churn cycles")
    p.add_argument("--victim-prime-hold-ms", type=int, default=3000, help="Prime hold time for victim")
    p.add_argument("--victim-revisit-every-cycles", type=int, default=10, help="Revisit victim every N churn cycles")
    p.add_argument("--victim-revisit-hold-ms", type=int, default=600, help="Victim revisit hold time")
    p.add_argument("--drop-cache-every-s", type=float, default=20.0, help="Background drop_caches interval")
    p.add_argument("--summary-every-s", type=float, default=10.0, help="Background summary interval")
    p.add_argument("--trace-chunk-lines", type=int, default=100000, help="trace_pipe chunk line limit")
    p.add_argument("--trace-chunk-size-mb", type=int, default=50, help="trace_pipe chunk size limit")
    p.add_argument("--no-use-su", action="store_true", help="Disable --use-su when launching memstress")
    p.add_argument("--no-clear-logcat", action="store_true", help="Disable --clear-logcat when launching memstress")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    serial = str(args.serial).strip()
    if not serial:
        raise SystemExit("missing --serial and $FOLIO_2 is empty")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path.cwd() / "output" / f"refault_probe_{ts}_{serial}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = out_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "serial": serial,
        "start_host_ts": int(time.time()),
        "config": vars(args),
        "trace_dir": str(trace_dir),
    }
    (out_dir / "probe_manifest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    stop_event = threading.Event()

    def _handle(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    setup_cp = subprocess.run(
        ["bash", str(TRACE_SETUP_SCRIPT), serial],
        capture_output=True,
        text=True,
        check=False,
    )
    (out_dir / "trace_setup.stdout.txt").write_text(setup_cp.stdout or "", encoding="utf-8")
    (out_dir / "trace_setup.stderr.txt").write_text(setup_cp.stderr or "", encoding="utf-8")
    if setup_cp.returncode != 0:
        raise SystemExit(f"tracepoint setup failed rc={setup_cp.returncode}")

    trace_proc = subprocess.Popen(
        [
            "python3",
            str(TRACE_CAPTURE_SCRIPT),
            "--outdir",
            str(trace_dir),
            "--serial",
            serial,
            "--chunk-lines",
            str(int(args.trace_chunk_lines)),
            "--chunk-size-mb",
            str(int(args.trace_chunk_size_mb)),
        ],
        stdout=(out_dir / "trace_capture.stdout.txt").open("w", encoding="utf-8"),
        stderr=(out_dir / "trace_capture.stderr.txt").open("w", encoding="utf-8"),
        text=True,
    )

    memstress_cmd: List[str] = [
        "python3",
        str(MEMSTRESS_SCRIPT),
        "--serial",
        serial,
        "--out-dir",
        str(out_dir),
        "--max-cycles",
        str(int(args.max_cycles)),
        "--interval-s",
        str(int(args.interval_s)),
        "--package-file",
        str(Path(args.package_file).resolve()),
    ]
    if args.victim_package:
        for vp in args.victim_package:
            memstress_cmd.extend(["--victim-package", vp])
    memstress_cmd.extend([
        "--victim-revisit-every-cycles",
        str(int(args.victim_revisit_every_cycles)),
        "--victim-prime-hold-ms",
        str(int(args.victim_prime_hold_ms)),
        "--victim-revisit-hold-ms",
        str(int(args.victim_revisit_hold_ms)),
        "--burst-size",
        str(int(args.burst_size)),
        "--heavy-per-burst",
        str(int(args.heavy_per_burst)),
        "--hold-ms",
        str(int(args.hold_ms)),
        "--launch-gap-ms",
        str(int(args.launch_gap_ms)),
        "--cycle-sleep-ms",
        str(int(args.cycle_sleep_ms)),
    ])
    if args.heavy_package_file:
        memstress_cmd.extend(["--heavy-package-file", str(Path(args.heavy_package_file).resolve())])
    if not args.no_use_su:
        memstress_cmd.append("--use-su")
    if not args.no_clear_logcat:
        memstress_cmd.append("--clear-logcat")

    memstress_stdout = (out_dir / "memstress_host_stdout.txt").open("w", encoding="utf-8")
    memstress_stderr = (out_dir / "memstress_host_stderr.txt").open("w", encoding="utf-8")
    memstress_proc = subprocess.Popen(memstress_cmd, stdout=memstress_stdout, stderr=memstress_stderr, text=True)

    summary_cmd = [
        "python3",
        str(SUMMARY_SCRIPT),
        "--run-dir",
        str(out_dir),
        "--trace-dir",
        str(trace_dir),
        "--watch",
        "--every-s",
        str(float(args.summary_every_s)),
        "--until-pid",
        str(memstress_proc.pid),
    ]
    summary_stdout = (out_dir / "summary_watch.stdout.txt").open("w", encoding="utf-8")
    summary_stderr = (out_dir / "summary_watch.stderr.txt").open("w", encoding="utf-8")
    summary_proc = subprocess.Popen(summary_cmd, stdout=summary_stdout, stderr=summary_stderr, text=True)

    drop_sidecar = DropCachesSidecar(
        serial=serial,
        interval_s=float(args.drop_cache_every_s),
        log_path=out_dir / "drop_caches.jsonl",
        stop_event=stop_event,
    )
    drop_sidecar.start()

    try:
        rc = memstress_proc.wait()
    finally:
        stop_event.set()
        drop_sidecar.join(timeout=max(3.0, float(args.drop_cache_every_s) + 1.0))

        try:
            subprocess.run(
                ["python3", str(SUMMARY_SCRIPT), "--run-dir", str(out_dir), "--trace-dir", str(trace_dir)],
                check=False,
                stdout=summary_stdout,
                stderr=summary_stderr,
                text=True,
            )
        finally:
            try:
                summary_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                summary_proc.terminate()
                summary_proc.wait(timeout=5)

        if trace_proc.poll() is None:
            trace_proc.terminate()
            try:
                trace_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                trace_proc.kill()
                trace_proc.wait(timeout=5)

        memstress_stdout.close()
        memstress_stderr.close()
        summary_stdout.close()
        summary_stderr.close()

    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
