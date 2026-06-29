#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a long-running Android THP anon fallback sampling experiment with monkey.

This script is host-side: it uses `adb` to talk to the device.

Outputs (under --out-dir):
- raw_samples.csv
- derived.csv
- summary.md
- monkey/ (logcat + monkey stdout/stderr + a few dumpsys snapshots)
- run_manifest.json
"""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, IO, List, Optional, Sequence

from utils.adb_utils import LogcatHandle, adb_base, adb_shell, ensure_adb_works, resolve_serials, start_logcat
from utils.adb_utils import stop_monkey_best_effort
from utils.device_prep import ensure_awake_unlocked_and_stay_awake
from utils.experiment_utils import ensure_out_dir, maybe_install_apks, run_setup_cmds
from utils.pkg_utils import read_package_file, unique_preserve_order
from utils.sampling_utils import DEFAULT_COUNTERS, DEFAULT_STATS_DIR, run_derive_metrics, sample_loop, write_run_manifest
from utils.thp_utils import ensure_thp_mode_for_stats
from utils.task_pool import TaskPool


# === CONFIG (edit here) ===
CONFIG = {
    "duration_s": 6 * 3600,
    "interval_s": 60,
    "stats_dir": DEFAULT_STATS_DIR,
    "counters": list(DEFAULT_COUNTERS),
    "use_su": True,
    # Optional setup commands executed before sampling (repeatable).
    "setup_shell": [],
    # Ensure/check THP mode via <stats_dir_parent>/enabled.
    "thp_ensure": {
        "enabled": True,
        "desired_mode": "always",
        "retries": 3,
        "retry_sleep_s": 2,
    },
    # Monkey workload config.
    "monkey": {
        "mode": "global",  # global|package
        "packages": [],
        "package_file": None,
        "throttle_ms": 75,
        "events": None,  # None => auto compute from duration+throttle
        "extra_flags": "--ignore-native-crashes --ignore-crashes --ignore-timeouts --ignore-security-exceptions",
        "clear_logcat": True,
        "device_prepare": True,
        "enable_tracing_on": True,
        "device_prepare_retries": 3,
        "device_prepare_retry_s": 2,
    },
    # Sampling retries.
    "sample_retries": 2,
    "sample_retry_sleep_s": 2,
}


def compute_monkey_events(*, duration_s: int, throttle_ms: int) -> int:
    est = int((duration_s * 1000) / max(1, throttle_ms))
    return max(est, 10_000)


@dataclass
class MonkeyHandle:
    proc: subprocess.Popen
    logcat: LogcatHandle
    stdout_f: IO[str]
    stderr_f: IO[str]

    def stop(self, *, serial: str) -> Optional[int]:
        stop_monkey_best_effort(serial)

        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        finally:
            try:
                self.logcat.stop()
            except Exception:
                pass
            for fh in (self.stdout_f, self.stderr_f):
                try:
                    fh.close()
                except Exception:
                    pass
        return self.proc.returncode


def start_monkey(
    *,
    serial: str,
    out_dir: Path,
    mode: str,
    pkgs: Sequence[str],
    throttle_ms: int,
    events: int,
    extra_flags: str,
    clear_logcat: bool,
) -> MonkeyHandle:
    monkey_out = out_dir / "monkey"
    monkey_out.mkdir(parents=True, exist_ok=True)

    logcat = start_logcat(serial, monkey_out, clear_logcat=clear_logcat)

    # Snapshot a few state points for later debugging.
    (monkey_out / "dumpsys_power.txt").write_text(
        adb_shell(serial, "dumpsys power", use_su=False, timeout_s=30, tty=False, check=False) or "",
        encoding="utf-8",
    )
    (monkey_out / "dumpsys_activity_top.txt").write_text(
        adb_shell(serial, "dumpsys activity top", use_su=False, timeout_s=60, tty=False, check=False) or "",
        encoding="utf-8",
    )

    cmd: List[str] = adb_base(serial) + ["shell", "monkey"]

    if mode == "package":
        if not pkgs:
            raise ValueError("--monkey=package requires at least one --monkey-package/--monkey-package-file entry")
        for pkg in pkgs:
            cmd += ["-p", pkg]
    elif mode != "global":
        raise ValueError("--monkey must be one of: global, package")

    cmd += ["--throttle", str(max(0, throttle_ms))]

    if extra_flags:
        cmd += shlex.split(extra_flags)

    cmd.append(str(max(1, events)))

    stdout_f = (monkey_out / "monkey_stdout.txt").open("w", encoding="utf-8")
    stderr_f = (monkey_out / "monkey_stderr.txt").open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=stdout_f, stderr=stderr_f, text=True)
    return MonkeyHandle(proc=proc, logcat=logcat, stdout_f=stdout_f, stderr_f=stderr_f)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Android THP anon fallback sampler + monkey (adb host-side)")

    p.add_argument(
        "--serial",
        action="append",
        default=[],
        help="Target device serial (repeatable, also supports comma-separated). If omitted, auto-detect if exactly one device.",
    )
    p.add_argument("--all-devices", action="store_true", help="Run on all `adb devices` in 'device' state")
    p.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="Max parallel devices (threads). Default: 0 => len(serials).",
    )
    p.add_argument("--out-dir", default=None, help="Output directory (default: ./output/thp_monkey_<timestamp>)")

    p.add_argument("--duration-s", type=int, default=CONFIG["duration_s"], help="Total duration seconds")
    p.add_argument("--interval-s", type=int, default=CONFIG["interval_s"], help="Sampling interval seconds")
    p.add_argument("--stats-dir", default=CONFIG["stats_dir"], help="Stats dir for counters")
    p.add_argument("--counters", default=",".join(CONFIG["counters"]), help="Comma-separated counter files to sample")

    p.add_argument(
        "--use-su",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["use_su"],
        help="Use su -c when running setup / reading stats / ensuring THP mode",
    )
    p.add_argument("--setup-shell", action="append", default=None, help="Device-side shell cmd to run before sampling (repeatable)")

    p.add_argument(
        "--thp-ensure-mode",
        default=CONFIG["thp_ensure"]["desired_mode"],
        help="Ensure this mode in <stats_dir_parent>/enabled (use 'none' to check-only)",
    )
    p.add_argument("--no-thp-ensure", action="store_true", help="Skip THP mode ensure/check workflow")
    p.add_argument("--thp-ensure-retries", type=int, default=CONFIG["thp_ensure"]["retries"])
    p.add_argument("--thp-ensure-retry-s", type=int, default=CONFIG["thp_ensure"]["retry_sleep_s"])

    p.add_argument("--apk-dir", default=None, help="Directory of *.apk to install before running")

    p.add_argument("--monkey", default=CONFIG["monkey"]["mode"], choices=["global", "package"], help="Monkey mode")
    p.add_argument("--monkey-package", action="append", default=None, dest="monkey_package",
                   help="Package name for monkey package mode (repeatable for multiple packages)")
    p.add_argument("--monkey-package-file", default=CONFIG["monkey"]["package_file"], help="File with packages (one per line)")
    p.add_argument("--monkey-throttle-ms", type=int, default=CONFIG["monkey"]["throttle_ms"])
    p.add_argument("--monkey-events", type=int, default=CONFIG["monkey"]["events"], help="Total monkey events; default: auto by duration")
    p.add_argument("--monkey-extra", default=CONFIG["monkey"]["extra_flags"], help="Extra monkey flags (string, shlex-split)")
    p.add_argument(
        "--clear-logcat",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["monkey"]["clear_logcat"],
        help="Clear logcat before starting monkey/logcat collection",
    )

    p.add_argument(
        "--device-prepare",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["monkey"]["device_prepare"],
        help="Try to wake/unlock + keep screen on before starting workload",
    )
    p.add_argument(
        "--enable-tracing-on",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["monkey"]["enable_tracing_on"],
        help="During device prepare, write 1 to /sys/kernel/tracing/tracing_on (default: on). No events are enabled, so overhead is near zero.",
    )
    p.add_argument("--device-prepare-retries", type=int, default=CONFIG["monkey"]["device_prepare_retries"])
    p.add_argument("--device-prepare-retry-s", type=int, default=CONFIG["monkey"]["device_prepare_retry_s"])

    p.add_argument("--sample-retries", type=int, default=CONFIG["sample_retries"], help="Sampling retries per tick")
    p.add_argument("--sample-retry-sleep-s", type=int, default=CONFIG["sample_retry_sleep_s"], help="Sleep seconds between sampling retries")

    return p.parse_args(argv)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def run_one_device(
    *,
    serial: str,
    out_dir: Path,
    args: argparse.Namespace,
    stop_event: threading.Event,
) -> Dict:
    scripts_dir = Path(__file__).resolve().parent

    counters = [x.strip() for x in str(args.counters).split(",") if x.strip()]
    setup_cmds = CONFIG["setup_shell"] if args.setup_shell is None else list(args.setup_shell)

    monkey_pkgs: List[str] = []
    if args.monkey_package:
        monkey_pkgs.extend(args.monkey_package)
    monkey_pkgs.extend(read_package_file(args.monkey_package_file))
    monkey_pkgs = unique_preserve_order(monkey_pkgs)

    duration_s = max(1, int(args.duration_s))
    events = args.monkey_events
    if events is None:
        events = compute_monkey_events(duration_s=duration_s, throttle_ms=int(args.monkey_throttle_ms))

    manifest: Dict = {
        "serial": serial,
        "start_host_ts": int(time.time()),
        "status": "init",
        "config": {
            "duration_s": duration_s,
            "interval_s": int(args.interval_s),
            "stats_dir": args.stats_dir,
            "counters": counters,
            "use_su": bool(args.use_su),
            "setup_shell": setup_cmds,
            "thp_ensure": {
                "enabled": (not args.no_thp_ensure),
                "desired_mode": args.thp_ensure_mode,
                "retries": int(args.thp_ensure_retries),
                "retry_sleep_s": int(args.thp_ensure_retry_s),
            },
            "apk_dir": args.apk_dir,
            "monkey": {
                "mode": args.monkey,
                "packages": monkey_pkgs,
                "throttle_ms": int(args.monkey_throttle_ms),
                "events": int(events),
                "extra_flags": args.monkey_extra,
                "clear_logcat": bool(args.clear_logcat),
                "device_prepare": bool(args.device_prepare),
                "enable_tracing_on": bool(args.enable_tracing_on),
            },
        },
        "samples": 0,
        "sample_errors": 0,
        "monkey_exit_code": None,
        "end_host_ts": None,
        "error": "",
    }
    write_run_manifest(out_dir / "run_manifest.json", manifest)

    monkey_rc: Optional[int] = None
    try:
        maybe_install_apks(scripts_dir=scripts_dir, apk_dir=args.apk_dir, serial=serial, out_dir=out_dir)

        run_setup_cmds(serial, setup_cmds, use_su=bool(args.use_su), log_path=out_dir / "setup_log.txt")

        if not args.no_thp_ensure:
            thp_result = ensure_thp_mode_for_stats(
                serial,
                stats_dir=args.stats_dir,
                use_su=bool(args.use_su),
                desired_mode=args.thp_ensure_mode,
                retries=int(args.thp_ensure_retries),
                retry_sleep_s=int(args.thp_ensure_retry_s),
                log_path=out_dir / "thp_ensure_log.txt",
            )
            manifest["thp_ensure_result"] = thp_result
            write_run_manifest(out_dir / "run_manifest.json", manifest)

        if args.device_prepare:
            ensure_awake_unlocked_and_stay_awake(
                serial,
                out_dir=out_dir / "monkey",
                retries=int(args.device_prepare_retries),
                retry_sleep_s=int(args.device_prepare_retry_s),
                enable_tracing_on=bool(args.enable_tracing_on),
            )

        manifest["status"] = "running"
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        monkey = start_monkey(
            serial=serial,
            out_dir=out_dir,
            mode=args.monkey,
            pkgs=monkey_pkgs,
            throttle_ms=int(args.monkey_throttle_ms),
            events=int(events),
            extra_flags=str(args.monkey_extra or ""),
            clear_logcat=bool(args.clear_logcat),
        )

        try:
            n, nerr = sample_loop(
                serial=serial,
                stats_dir=args.stats_dir,
                counters=counters,
                use_su=bool(args.use_su),
                interval_s=max(1, int(args.interval_s)),
                duration_s=duration_s,
                out_csv=out_dir / "raw_samples.csv",
                retries=max(0, int(args.sample_retries)),
                retry_sleep_s=max(0, int(args.sample_retry_sleep_s)),
                stop_event=stop_event,
            )
        finally:
            monkey_rc = monkey.stop(serial=serial)

        manifest["monkey_exit_code"] = monkey_rc
        manifest["samples"] = n
        manifest["sample_errors"] = nerr
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        run_derive_metrics(scripts_dir=scripts_dir, out_dir=out_dir)

        manifest["status"] = "finished" if not stop_event.is_set() else "stopped"
        manifest["end_host_ts"] = int(time.time())
        write_run_manifest(out_dir / "run_manifest.json", manifest)
        return {
            "serial": serial,
            "out_dir": str(out_dir),
            "samples": n,
            "sample_errors": nerr,
            "monkey_rc": monkey_rc,
        }
    except Exception as e:
        manifest["status"] = "failed"
        manifest["end_host_ts"] = int(time.time())
        manifest["monkey_exit_code"] = monkey_rc
        manifest["error"] = str(e)
        write_run_manifest(out_dir / "run_manifest.json", manifest)
        raise


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    serials = resolve_serials(args.serial, all_devices=bool(args.all_devices))
    multi = len(serials) > 1

    top_out_dir = ensure_out_dir(args.out_dir, default_prefix="thp_monkey")
    jobs = int(args.jobs) if int(args.jobs) > 0 else max(1, len(serials))

    if multi:
        print(f"[fleet] devices={len(serials)} jobs={jobs} out_dir={top_out_dir}")

    pool = TaskPool(max_workers=jobs)
    futures = {}
    try:
        for serial in serials:
            out_dir = (top_out_dir / serial) if multi else top_out_dir
            futures[serial] = pool.submit(
                serial,
                run_one_device,
                serial=serial,
                out_dir=out_dir,
                args=args,
                stop_event=stop_event,
            )

        results = pool.gather(futures, stop_event=stop_event, fail_fast=True)
    finally:
        pool.close()

    ok = all(r.ok for r in results)
    for r in results:
        if r.ok and r.value:
            v = r.value
            print(
                f"[{v['serial']}] out_dir={v['out_dir']} samples={v['samples']} "
                f"errors={v['sample_errors']} monkey_rc={v['monkey_rc']}"
            )
        else:
            print(f"[{r.name}] FAIL: {r.error}", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
