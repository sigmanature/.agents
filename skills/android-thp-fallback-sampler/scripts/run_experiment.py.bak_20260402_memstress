#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a long-running Android THP 64KB/16KB anon fallback sampling experiment.

This script is host-side: it uses `adb` to talk to the device.
It can optionally:
- batch install APKs (by calling apk_batch_install.py)
- run monkey workload (by calling run_monkey_and_collect_logs.sh)
- sample /sys/kernel/mm/transparent_hugepage/hugepages-*/stats/* periodically

Outputs (under --out-dir):
- raw_samples.csv
- derived.csv
- summary.md
- monkey/ (if monkey enabled)
- run_manifest.json

Designed for stability: retries ADB sampling on transient failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pty
import select
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_STATS_DIR = "/sys/kernel/mm/transparent_hugepage/hugepages-16kB/stats"
DEFAULT_COUNTERS = [
    "anon_fault_alloc",
    "anon_fault_fallback",
    "anon_fault_fallback_charge",
    "split",
    "swpin",
    "swpout",
    "zswpout",
]


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run(cmd: List[str], timeout_s: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=check)


def _run_with_pty(cmd: List[str], timeout_s: int = 60) -> subprocess.CompletedProcess:
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)

    chunks: List[bytes] = []
    deadline = time.time() + timeout_s

    try:
        while True:
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=5)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s)

            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    chunks.append(data)
                elif proc.poll() is not None:
                    break

            if proc.poll() is not None and master_fd not in ready:
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    stdout = b"".join(chunks).decode("utf-8", "ignore")
    return subprocess.CompletedProcess(cmd, proc.returncode or 0, stdout=stdout, stderr="")


def adb_devices() -> List[str]:
    cp = _run(["adb", "devices"], timeout_s=20, check=True)
    serials: List[str] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def resolve_serial(user_serial: Optional[str]) -> str:
    if user_serial:
        return user_serial
    serials = adb_devices()
    if len(serials) == 1:
        return serials[0]
    if not serials:
        raise RuntimeError("No device found (adb devices shows none in 'device' state)")
    raise RuntimeError("Multiple devices detected; pass --serial. Devices: " + ", ".join(serials))


def adb_base(serial: str) -> List[str]:
    return ["adb", "-s", serial]


def adb_shell(serial: str, cmd: str, use_su: bool, timeout_s: int = 30,
              tty: bool = False) -> str:
    base = adb_base(serial)
    if use_su:
        # Run via: su -c 'sh -c <cmd>' so redirection/pipes work.
        wrapped = f"sh -c {shlex.quote(cmd)}"
        shell_cmd = ["shell"]
        if tty:
            shell_cmd.extend(["-t", "-t"])
            remote_cmd = f"su -c {shlex.quote(wrapped)}"
            cp = _run_with_pty(base + shell_cmd + [remote_cmd], timeout_s=timeout_s)
        else:
            cp = _run(base + shell_cmd + ["su", "-c", wrapped], timeout_s=timeout_s)
    else:
        shell_cmd = ["shell"]
        if tty:
            shell_cmd.extend(["-t", "-t"])
            cp = _run_with_pty(base + shell_cmd + [cmd], timeout_s=timeout_s)
        else:
            # For non-root commands, let `adb shell` execute the remote command string directly.
            # This avoids an extra `sh -c` layer that can mangle argv-style tools such as
            # `input` / `wm` on some Android builds.
            cp = _run(base + shell_cmd + [cmd], timeout_s=timeout_s)

    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "adb shell failed").strip())
    return cp.stdout


def adb_shell_retry(serial: str, cmd: str, use_su: bool, timeout_s: int,
                    retries: int, retry_sleep_s: int, tty: bool = False) -> str:
    last_err: Optional[Exception] = None
    for i in range(max(1, retries + 1)):
        try:
            return adb_shell(serial, cmd, use_su=use_su, timeout_s=timeout_s, tty=tty)
        except Exception as e:
            last_err = e
            if i + 1 < max(1, retries + 1):
                time.sleep(max(0, retry_sleep_s))
    raise RuntimeError(str(last_err) if last_err else "adb_shell_retry failed")


def is_device_awake(serial: str) -> Tuple[bool, str]:
    try:
        out = adb_shell(serial, "dumpsys power", use_su=False, timeout_s=30)
    except Exception as e:
        return False, f"ERR:{e}"

    wake_lines = [ln.strip() for ln in out.splitlines() if "mWakefulness" in ln]
    awake = any(("Awake" in ln) or ("mWakefulness=1" in ln) for ln in wake_lines)

    if wake_lines:
        summary = " | ".join(wake_lines[:4])
    else:
        summary = " | ".join(out.splitlines()[:3]).strip()
    return awake, summary


def prepare_device_for_monkey(serial: str, out_dir: Path, retries: int, retry_sleep_s: int) -> None:
    """Best-effort device prep for stable monkey runs.

    Ensures wake/unlock attempts and requests stay-awake behavior.
    Raises RuntimeError if the device is not awake after retries.
    """
    log_path = out_dir / "device_prepare_log.txt"
    cmds = [
        "input keyevent KEYCODE_WAKEUP || true",
        "wm dismiss-keyguard || true",
        "input keyevent KEYCODE_MENU || true",
        "input swipe 300 1400 300 400 200 || true",
        "svc power stayon true || true",
        "settings put global stay_on_while_plugged_in 3 || true",
        "settings put system screen_off_timeout 1800000 || true",
    ]

    with log_path.open("a", encoding="utf-8") as f:
        for attempt in range(1, max(1, retries) + 1):
            f.write(f"\n[{attempt}] {datetime.now().isoformat()}\n")
            for cmd in cmds:
                f.write(f"$ {cmd}\n")
                try:
                    out = adb_shell_retry(
                        serial,
                        cmd,
                        use_su=False,
                        timeout_s=20,
                        retries=1,
                        retry_sleep_s=1,
                    )
                    if out.strip():
                        f.write(out)
                        if not out.endswith("\n"):
                            f.write("\n")
                except Exception as e:
                    f.write(f"ERR: {e}\n")

            awake, wake_out = is_device_awake(serial)
            if wake_out:
                f.write(f"wake_out={wake_out}\n")
            f.write(f"awake={awake}\n")
            f.flush()
            if awake:
                return
            if attempt < max(1, retries):
                time.sleep(max(0, retry_sleep_s))

    raise RuntimeError(
        f"device prepare failed: device did not reach awake state after {max(1, retries)} attempts"
    )


def infer_enabled_path_from_stats_dir(stats_dir: str) -> Optional[str]:
    x = stats_dir.rstrip("/")
    if not x.endswith("/stats"):
        return None
    return x[: -len("/stats")] + "/enabled"


def ensure_thp_mode_for_stats(serial: str, stats_dir: str, use_su: bool, desired_mode: str,
                              retries: int, retry_sleep_s: int, log_path: Path) -> Dict[str, str]:
    """Ensure THP mode for the stats directory's sibling `enabled` file.

    Returns a result dict suitable for run_manifest.json.
    """
    result: Dict[str, str] = {
        "stats_dir": stats_dir,
        "status": "skipped",
        "enabled_path": "",
        "before": "",
        "after": "",
        "desired": desired_mode,
        "reason": "",
    }

    enabled_path = infer_enabled_path_from_stats_dir(stats_dir)
    if not enabled_path:
        result["reason"] = "stats_dir does not end with /stats"
        return result

    result["enabled_path"] = enabled_path

    def _read_enabled() -> str:
        out = adb_shell_retry(
            serial,
            f"cat {enabled_path}",
            use_su=use_su,
            timeout_s=20,
            retries=max(0, retries),
            retry_sleep_s=max(0, retry_sleep_s),
            tty=use_su,
        )
        return out.strip()

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] stats_dir={stats_dir} enabled_path={enabled_path}\n")
        before = _read_enabled()
        result["before"] = before
        f.write(f"before: {before}\n")

        desired = (desired_mode or "").strip().lower()
        if not desired or desired == "none":
            result["status"] = "checked"
            result["after"] = before
            result["reason"] = "desired mode is none"
            f.write("desired mode is none; check-only\n")
            return result

        # Some devices only allow sysfs writes from an interactive root shell.
        cmd = f"echo {shlex.quote(desired)} > {enabled_path}"
        adb_shell_retry(
            serial,
            cmd,
            use_su=use_su,
            timeout_s=20,
            retries=max(0, retries),
            retry_sleep_s=max(0, retry_sleep_s),
            tty=use_su,
        )

        after = _read_enabled()
        result["after"] = after
        f.write(f"after: {after}\n")

        if f"[{desired}]" not in after and desired not in after.split():
            result["status"] = "failed"
            result["reason"] = "desired mode not active after write"
            raise RuntimeError(
                f"THP mode ensure failed for {enabled_path}: expected '{desired}' active, got '{after}'"
            )

        result["status"] = "ensured"
        result["reason"] = "ok"
        return result


def ensure_out_dir(out_dir: Optional[str]) -> Path:
    p = Path(out_dir) if out_dir else Path("output") / f"thp_fallback_{_now_ts()}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_setup_cmds(serial: str, setup_cmds: List[str], use_su: bool, log_path: Path) -> None:
    if not setup_cmds:
        return

    with log_path.open("w", encoding="utf-8") as f:
        for i, cmd in enumerate(setup_cmds, start=1):
            f.write(f"[{i}] {cmd}\n")
            try:
                out = adb_shell(serial, cmd, use_su=use_su, timeout_s=30)
                if out.strip():
                    f.write(out)
                    if not out.endswith("\n"):
                        f.write("\n")
            except Exception as e:
                f.write(f"ERROR: {e}\n")


def maybe_install_apks(skill_dir: Path, apk_dir: Optional[str], serial: str, out_dir: Path) -> Optional[Path]:
    if not apk_dir:
        return None

    apk_dir_path = Path(apk_dir)
    if not apk_dir_path.exists():
        raise FileNotFoundError(f"apk dir not found: {apk_dir}")

    installer = skill_dir / "apk_batch_install.py"
    install_out = out_dir / "apk_install"
    install_out.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(installer), str(apk_dir_path), "--serial", serial, "--output-dir", str(install_out)]
    cp = _run(cmd, timeout_s=60 * 60, check=False)
    (install_out / "installer_stdout.txt").write_text(cp.stdout, encoding="utf-8")
    (install_out / "installer_stderr.txt").write_text(cp.stderr, encoding="utf-8")
    if cp.returncode not in (0, 1):
        raise RuntimeError(f"apk install tool failed rc={cp.returncode}. See {install_out}")

    return install_out


def compute_monkey_events(duration_s: int, throttle_ms: int) -> int:
    # Approx: 1 event per throttle interval.
    est = int((duration_s * 1000) / max(1, throttle_ms))
    return max(est, 10_000)


def start_monkey(skill_dir: Path, serial: str, out_dir: Path, mode: str, pkgs: Optional[List[str]],
                 throttle_ms: int, events: Optional[int], extra: str, clear_logcat: bool) -> Optional[subprocess.Popen]:
    if mode == "none":
        return None

    monkey_script = skill_dir / "run_monkey_and_collect_logs.sh"
    monkey_out = out_dir / "monkey"
    monkey_out.mkdir(parents=True, exist_ok=True)

    args = ["bash", str(monkey_script), "--serial", serial, "--out", str(monkey_out), "--throttle", str(throttle_ms)]

    if clear_logcat:
        args.append("--clear-logcat")

    if mode == "global":
        args.append("--global")
    elif mode == "package":
        if not pkgs:
            raise ValueError("--monkey-package is required when --monkey=package")
        for pkg in pkgs:
            args += ["--package", pkg]
    else:
        raise ValueError("--monkey must be one of: none, global, package")

    if events is not None:
        args += ["--events", str(events)]

    if extra:
        args += ["--extra", extra]

    # Run in background.
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop_monkey_best_effort(serial: str) -> None:
    # Try to stop monkey process on device if still running.
    base = adb_base(serial)
    _run(base + ["shell", "sh", "-c", "pkill -f com.android.commands.monkey || true"], timeout_s=10, check=False)


@dataclass
class Sample:
    host_ts: int
    device_ts: Optional[int]
    values: Dict[str, Optional[int]]
    error: str = ""


def parse_kv_lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_counters_once(serial: str, stats_dir: str, counters: List[str], use_su: bool) -> Sample:
    host_ts = int(time.time())

    # One adb call for ts + all counters.
    parts = [
        "ts=$(date +%s)",
        "echo device_ts=$ts",
    ]
    for c in counters:
        # Print empty if missing.
        parts.append(f"v=$(cat {stats_dir}/{c} 2>/dev/null || echo '')")
        parts.append(f"echo {c}=$v")

    script = "; ".join(parts)

    try:
        out = adb_shell(serial, script, use_su=use_su, timeout_s=20)
        kv = parse_kv_lines(out)
        dev_ts = int(kv.get("device_ts")) if kv.get("device_ts", "").isdigit() else None
        values: Dict[str, Optional[int]] = {}
        for c in counters:
            s = kv.get(c, "")
            values[c] = int(s) if s.isdigit() else None
        return Sample(host_ts=host_ts, device_ts=dev_ts, values=values, error="")
    except Exception as e:
        return Sample(host_ts=host_ts, device_ts=None, values={c: None for c in counters}, error=str(e))


def sample_loop(serial: str, stats_dir: str, counters: List[str], use_su: bool,
                interval_s: int, duration_s: int, out_csv: Path,
                retries: int, retry_sleep_s: int, monkey_proc: Optional[subprocess.Popen]) -> Tuple[int, int]:
    """Returns (num_samples, num_errors)."""

    fieldnames = ["host_ts", "device_ts", "error"] + counters

    t0 = time.time()
    t_end = t0 + duration_s
    next_t = t0

    num = 0
    num_err = 0

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        while True:
            now = time.time()
            if now >= t_end:
                break

            # align schedule
            if now < next_t:
                time.sleep(min(next_t - now, 1.0))
                continue

            s: Optional[Sample] = None
            for attempt in range(retries + 1):
                s = read_counters_once(serial, stats_dir, counters, use_su=use_su)
                if not s.error:
                    break
                time.sleep(retry_sleep_s)

            assert s is not None
            row = {
                "host_ts": s.host_ts,
                "device_ts": s.device_ts if s.device_ts is not None else "",
                "error": s.error,
            }
            for c in counters:
                v = s.values.get(c)
                row[c] = v if v is not None else ""
            w.writerow(row)
            f.flush()

            num += 1
            if s.error:
                num_err += 1

            # If monkey already finished, we still keep sampling until duration ends.
            _ = monkey_proc.poll() if monkey_proc else None

            next_t += interval_s

    return num, num_err


def run_derive(skill_dir: Path, out_dir: Path) -> None:
    derive = skill_dir / "derive_metrics.py"
    cmd = [sys.executable, str(derive), str(out_dir / "raw_samples.csv"), "--out-dir", str(out_dir)]
    cp = _run(cmd, timeout_s=120, check=False)
    (out_dir / "derive_stdout.txt").write_text(cp.stdout, encoding="utf-8")
    (out_dir / "derive_stderr.txt").write_text(cp.stderr, encoding="utf-8")
    if cp.returncode != 0:
        raise RuntimeError(f"derive_metrics failed rc={cp.returncode}. See derive_stderr.txt")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Android THP 64KB anon fallback sampler (adb host-side)")
    p.add_argument("--serial", default=None, help="Target device serial. Auto-detect if exactly one device.")
    p.add_argument("--duration-s", type=int, default=6 * 3600, help="Total sampling duration seconds (default: 6h)")
    p.add_argument("--interval-s", type=int, default=60, help="Sampling interval seconds (default: 60)")
    p.add_argument("--stats-dir", default=DEFAULT_STATS_DIR, help="Stats dir (default: hugepages-64kB stats)")
    p.add_argument("--counters", default=",".join(DEFAULT_COUNTERS), help="Comma-separated counter files to sample")
    p.add_argument("--use-su", action="store_true", help="Use su -c when running setup and reading stats")
    p.add_argument("--setup-shell", action="append", default=[], help="Device-side shell cmd to run before sampling (repeatable)")
    p.add_argument("--thp-ensure-mode", default="always",
                   help="Ensure this mode in <stats_dir_parent>/enabled before sampling (default: always; use 'none' to check-only)")
    p.add_argument("--thp-ensure-retries", type=int, default=3,
                   help="Retries for THP mode read/write checks")
    p.add_argument("--thp-ensure-retry-s", type=int, default=2,
                   help="Sleep seconds between THP mode ensure retries")
    p.add_argument("--no-thp-ensure", action="store_true",
                   help="Skip THP mode ensure/check workflow")

    p.add_argument("--apk-dir", default=None, help="Directory of *.apk to install before running")

    p.add_argument("--monkey", default="none", choices=["none", "global", "package"], help="Monkey mode")
    p.add_argument("--monkey-package", action="append", default=None, dest="monkey_package",
                   help="Package name for monkey package mode (repeatable for multiple packages)")
    p.add_argument("--monkey-package-file", default=None,
                   help="Path to file with package names (one per line, # comments allowed)")
    p.add_argument("--monkey-throttle-ms", type=int, default=75, help="Monkey throttle ms")
    p.add_argument("--monkey-events", type=int, default=None, help="Monkey events; default computed from duration/throttle")
    p.add_argument("--monkey-extra", default="", help="Extra monkey flags appended verbatim")
    p.add_argument("--clear-logcat", action="store_true", help="Clear logcat buffers before monkey (destructive)")
    p.add_argument("--device-prepare", dest="device_prepare", action="store_true", default=True,
                   help="Before monkey: wake/unlock device and set stay-awake (default: enabled)")
    p.add_argument("--no-device-prepare", dest="device_prepare", action="store_false",
                   help="Disable pre-monkey wake/unlock/stay-awake preparation")
    p.add_argument("--device-prepare-retries", type=int, default=5,
                   help="Retries for pre-monkey device prepare checks")
    p.add_argument("--device-prepare-retry-s", type=int, default=3,
                   help="Sleep seconds between pre-monkey prepare retries")
    p.add_argument("--monkey-start-retries", type=int, default=2,
                   help="Retries when monkey runner exits immediately after start")
    p.add_argument("--monkey-start-retry-s", type=int, default=5,
                   help="Sleep seconds between monkey start retries")

    p.add_argument("--out-dir", default=None, help="Output directory")
    p.add_argument("--sample-retries", type=int, default=2, help="Retries per sample on adb failure")
    p.add_argument("--retry-sleep-s", type=int, default=2, help="Sleep between sample retries")

    args = p.parse_args(argv)

    # Preflight: adb exists
    try:
        _ = _run(["adb", "version"], timeout_s=10, check=True)
    except Exception:
        print("ERROR: adb not found or not working in PATH", file=sys.stderr)
        return 2

    serial = resolve_serial(args.serial)

    out_dir = ensure_out_dir(args.out_dir)
    (out_dir / "host_start_ts.txt").write_text(str(int(time.time())) + "\n", encoding="utf-8")

    skill_dir = Path(__file__).resolve().parent

    # Save manifest early.
    manifest = {
        "serial": serial,
        "start_host_ts": int(time.time()),
        "args": vars(args),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Optional: install apks
    if args.apk_dir:
        maybe_install_apks(skill_dir, args.apk_dir, serial=serial, out_dir=out_dir)

    # Setup commands
    run_setup_cmds(serial, args.setup_shell, use_su=args.use_su, log_path=out_dir / "setup_log.txt")

    # THP mode ensure/check workflow (requested for stable mTHP runs).
    if not args.no_thp_ensure:
        thp_mode = ensure_thp_mode_for_stats(
            serial=serial,
            stats_dir=args.stats_dir,
            use_su=args.use_su,
            desired_mode=args.thp_ensure_mode,
            retries=max(0, args.thp_ensure_retries),
            retry_sleep_s=max(0, args.thp_ensure_retry_s),
            log_path=out_dir / "thp_mode_log.txt",
        )
        manifest["thp_mode"] = thp_mode
        (out_dir / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    counters = [c.strip() for c in args.counters.split(",") if c.strip()]

    # Merge monkey packages from CLI and optional package file.
    monkey_pkgs: List[str] = list(args.monkey_package or [])
    if args.monkey_package_file:
        pkg_path = Path(args.monkey_package_file)
        if not pkg_path.exists():
            raise FileNotFoundError(f"monkey package file not found: {pkg_path}")
        for line in pkg_path.read_text(encoding="utf-8").splitlines():
            x = line.strip()
            if not x or x.startswith("#"):
                continue
            monkey_pkgs.append(x)

    # de-dup while preserving order
    monkey_pkgs = list(dict.fromkeys(monkey_pkgs))

    # Start monkey
    monkey_events = args.monkey_events
    if args.monkey != "none" and monkey_events is None:
        monkey_events = compute_monkey_events(args.duration_s, args.monkey_throttle_ms)

    monkey_proc: Optional[subprocess.Popen] = None
    if args.monkey != "none":
        if args.device_prepare:
            prepare_device_for_monkey(
                serial=serial,
                out_dir=out_dir,
                retries=max(1, args.device_prepare_retries),
                retry_sleep_s=max(0, args.device_prepare_retry_s),
            )

        start_attempts = max(1, args.monkey_start_retries)
        last_start_err = ""
        for attempt in range(1, start_attempts + 1):
            monkey_proc = start_monkey(
                skill_dir=skill_dir,
                serial=serial,
                out_dir=out_dir,
                mode=args.monkey,
                pkgs=monkey_pkgs,
                throttle_ms=args.monkey_throttle_ms,
                events=monkey_events,
                extra=args.monkey_extra,
                clear_logcat=args.clear_logcat,
            )

            # If it survives initial startup window, treat as started.
            time.sleep(3)
            rc = monkey_proc.poll() if monkey_proc else None
            if rc is None:
                break

            # Early exit: collect diagnostics and retry after re-prepare.
            try:
                out, err = monkey_proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            (out_dir / f"monkey_start_attempt_{attempt}.stdout.txt").write_text(out or "", encoding="utf-8")
            (out_dir / f"monkey_start_attempt_{attempt}.stderr.txt").write_text(err or "", encoding="utf-8")
            last_start_err = f"attempt={attempt}, rc={rc}"

            if args.device_prepare:
                prepare_device_for_monkey(
                    serial=serial,
                    out_dir=out_dir,
                    retries=max(1, args.device_prepare_retries),
                    retry_sleep_s=max(0, args.device_prepare_retry_s),
                )
            if attempt < start_attempts:
                time.sleep(max(0, args.monkey_start_retry_s))
            else:
                raise RuntimeError(f"monkey failed to stay alive after startup: {last_start_err}")
    else:
        monkey_proc = None

    # Sampling loop
    raw_csv = out_dir / "raw_samples.csv"
    n, nerr = sample_loop(
        serial=serial,
        stats_dir=args.stats_dir,
        counters=counters,
        use_su=args.use_su,
        interval_s=max(1, args.interval_s),
        duration_s=max(1, args.duration_s),
        out_csv=raw_csv,
        retries=max(0, args.sample_retries),
        retry_sleep_s=max(0, args.retry_sleep_s),
        monkey_proc=monkey_proc,
    )

    # Wait monkey to finish (best effort)
    if monkey_proc:
        try:
            stdout, stderr = monkey_proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            monkey_proc.kill()
            stdout, stderr = monkey_proc.communicate(timeout=10)
        (out_dir / "monkey_runner_stdout.txt").write_text(stdout or "", encoding="utf-8")
        (out_dir / "monkey_runner_stderr.txt").write_text(stderr or "", encoding="utf-8")

        # If still running on device, stop it.
        stop_monkey_best_effort(serial)

    # Derive
    run_derive(skill_dir, out_dir)

    # Final manifest update
    manifest["end_host_ts"] = int(time.time())
    manifest["samples"] = n
    manifest["sample_errors"] = nerr
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Done. out_dir: {out_dir}")
    print(f"Samples: {n} | sample_errors: {nerr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
