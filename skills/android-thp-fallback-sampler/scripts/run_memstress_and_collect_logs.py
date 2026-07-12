#!/usr/bin/env python3
"""Slim THP anon fallback sampler + memstress (adb host-side).

Outputs: raw_samples.csv / derived.csv / summary.md / run_manifest.json
         memstress/ (logcat + cycle_log.jsonl + dumpsys snapshots)

Usage:
  python3 run_memstress_and_collect_logs.py \
    --serial 18281FDF6007HB --max-cycles 120 --seed 20260617 \
    --package-file pkgs.txt --hold-ms 15 \
    --out-dir /tmp/run

  # Or re-run from a previous manifest:
  python3 run_memstress_and_collect_logs.py \
    --from-manifest last_run/run_manifest.json --seed 999
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from utils.adb_utils import adb_shell, adb_shell_cp, ensure_adb_works, start_logcat_stream
from utils.crash_signature import TargetCrashSignatureDetector
from utils.device_prep import ensure_awake_unlocked_and_stay_awake
from utils.pkg_utils import read_package_file
from utils.sampling_utils import DEFAULT_COUNTERS, DEFAULT_STATS_DIR, run_derive_metrics, sample_loop, write_run_manifest
from utils.thp_utils import ensure_thp_mode_for_stats
from utils.buddyinfo_utils import buddyinfo_sample_loop
from utils.vmstat_utils import derive_vmstat_csv, read_vmstat, vmstat_sample_loop
from utils.interactive import interactive_click_loop
from precondition_memory import run_precondition, load_packages_from_manifest as _load_pkgs_manifest
from fragmem_host import run_fragmem_precondition, stop_fragmem


# === CONFIG (overridable via CLI or --from-manifest) ===
CONFIG = {
    "max_cycles": 1200,
    "interval_s": 60,
    "stats_dir": DEFAULT_STATS_DIR,
    "counters": list(DEFAULT_COUNTERS),
    "use_su": True,
    "no_network_check": True,
    "device_prepare": {
        "enabled": True,
        "enable_tracing_on": True,
    },
    "memstress": {
        "burst_size": 1,
        "hold_ms": 200,
        "launch_gap_ms": 350,
        "cycle_sleep_ms": 1000,
        "seed": 12345,
        "clear_logcat": True,
        "mode": "launch_only",
    },
    "buddyinfo_interval_s": 5,
    "vmstat_interval_s": 60,
}


# --------------- helpers ---------------

def validate_packages(serial: str, pkgs: Sequence[str]) -> List[str]:
    """Return subset of pkgs installed on device."""
    out = adb_shell(serial, "pm list packages", timeout_s=30, check=False, use_su=False)
    installed = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            installed.add(line.split(":", 1)[1])
    return [p for p in pkgs if p in installed]


def _extract_component_from_line(line: str, pkg: str) -> Optional[str]:
    for token in line.split():
        if "/" not in token:
            continue
        token = token.rstrip(":")
        if token.startswith(pkg + "/") or token.startswith(pkg + ".") or token.startswith(pkg + "$"):
            return token
        if re.match(r"^[A-Za-z0-9_.]+/", token):
            return token
    return None


def _resolve_activity_from_dumpsys(output: str, pkg: str) -> Optional[str]:
    lines = output.splitlines()
    for index, raw_line in enumerate(lines):
        if "android.intent.action.MAIN:" not in raw_line:
            continue
        search_end = min(len(lines), index + 80)
        candidate: Optional[str] = None
        for next_line in lines[index + 1:search_end]:
            stripped = next_line.strip()
            if not stripped:
                continue
            if stripped.endswith(":") and not stripped.startswith("Category:"):
                break
            extracted = _extract_component_from_line(stripped, pkg)
            if extracted is not None:
                candidate = extracted
                continue
            if candidate is not None and 'Category: "android.intent.category.LAUNCHER"' in stripped:
                return candidate
    return None


def resolve_activity(serial: str, pkg: str) -> Optional[str]:
    out = adb_shell(serial, f"pm resolve-activity --brief {pkg}", timeout_s=10, check=False, use_su=False)
    for line in out.splitlines():
        line = line.strip()
        if "/" in line and not line.startswith("Error"):
            return line
    # fallback: cmd package resolve-activity
    out2 = adb_shell(serial,
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {pkg}",
        timeout_s=10, check=False, use_su=False)
    for line in out2.splitlines():
        line = line.strip()
        if "/" in line:
            return line
    out3 = adb_shell(serial, f"dumpsys package {pkg}", timeout_s=20, check=False, use_su=False)
    resolved = _resolve_activity_from_dumpsys(out3, pkg)
    if resolved is not None:
        return resolved
    return None


def start_activity(serial: str, component: str) -> str:
    # `adb shell` ultimately goes through the guest shell.  Activity names for
    # nested classes contain `$`, so the component must be quoted as one shell
    # word; passing it as a separate adb argv still lets the guest shell expand `$`.
    shell_cmd = "am start -W -n " + shlex.quote(component)
    cp = subprocess.run(["adb", "-s", serial, "shell", shell_cmd],
                        capture_output=True, text=True, timeout=30)
    output = ((cp.stdout or "") + (cp.stderr or "")).strip()
    bad_markers = ["Error:", "Exception", "not found", "does not exist", "result=", "Status: timeout"]
    if cp.returncode != 0 or any(marker in output for marker in bad_markers):
        raise RuntimeError(f"am start failed for {component}: rc={cp.returncode} output={output[:400]}")
    return output


def exit_to_home(serial: str):
    subprocess.run(["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_HOME"],
                   capture_output=True, timeout=10)


def force_stop_packages(serial: str, pkgs: Sequence[str]):
    for pkg in pkgs:
        subprocess.run(["adb", "-s", serial, "shell", "am", "force-stop", pkg],
                       capture_output=True, timeout=15)


class StopEvent:
    """Multi-thread stop signal — acts like threading.Event for compatibility."""
    def __init__(self):
        self._events: List[threading.Event] = []
    def add(self, e: threading.Event):
        self._events.append(e)
    def set(self):
        for e in self._events:
            e.set()
    def is_set(self):
        return any(e.is_set() for e in self._events)


# --------------- interactive ---------------

def launch_and_background(serial: str, component: str, hold_ms: int, mode: str):
    start_activity(serial, component)
    if mode == "interactive":
        time.sleep(0.6)
        interactive_click_loop(serial)
    time.sleep(max(0, hold_ms) / 1000.0)
    exit_to_home(serial)


# --------------- network ---------------

def ensure_network(serial: str):
    while True:
        cp = subprocess.run(
            ["adb", "-s", serial, "shell",
             "ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1 && echo online || echo offline"],
            capture_output=True, text=True, timeout=15)
        if cp.stdout and "online" in cp.stdout:
            print(f"[{serial}] network OK")
            return
        print(f"[{serial}] 设备未联网，请连接 WiFi 后继续...", file=sys.stderr)
        time.sleep(5)


# --------------- vmstat snapshot helpers ---------------

def record_vmstat_start(serial: str, out_dir: Path, use_su: bool) -> dict:
    """Record initial /proc/vmstat at prepare time; write vmstat_start.json."""
    values = read_vmstat(serial, use_su=use_su)
    (out_dir / "vmstat_start.json").write_text(
        json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return values


def record_vmstat_end(serial: str, out_dir: Path, use_su: bool) -> dict:
    """Record final /proc/vmstat after workload; write vmstat_end.json."""
    values = read_vmstat(serial, use_su=use_su)
    (out_dir / "vmstat_end.json").write_text(
        json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return values


# --------------- stats-dir auto-detect ---------------

def auto_detect_stats_dir(serial: str, use_su: bool) -> str:
    prefix = "su -c " if use_su else ""
    for size in ["16", "32", "64"]:
        path = f"/sys/kernel/mm/transparent_hugepage/hugepages-{size}kB/enabled"
        cp = subprocess.run(
            ["adb", "-s", serial, "shell", f"{prefix}cat {path}"],
            capture_output=True, text=True, timeout=10)
        if cp.stdout and "[always]" in cp.stdout:
            return f"/sys/kernel/mm/transparent_hugepage/hugepages-{size}kB/stats"
    return DEFAULT_STATS_DIR


# --------------- from-manifest ---------------

def load_manifest_args(manifest_path: str) -> dict:
    """Read run_manifest.json and extract CLI-compatible args dict."""
    data = json.loads(Path(manifest_path).read_text())
    cfg = data.get("config", data)
    ms = cfg.get("memstress", cfg)

    return {
        "max_cycles": cfg.get("max_cycles", CONFIG["max_cycles"]),
        "interval_s": cfg.get("interval_s", CONFIG["interval_s"]),
        "stats_dir": cfg.get("stats_dir", CONFIG["stats_dir"]),
        "counters": ",".join(cfg.get("counters", CONFIG["counters"])),
        "use_su": cfg.get("use_su", CONFIG["use_su"]),
        "no_network_check": cfg.get("no_network_check", CONFIG["no_network_check"]),
        "device_prepare": cfg.get("device_prepare", CONFIG["device_prepare"]["enabled"]),
        "enable_tracing_on": cfg.get("enable_tracing_on", CONFIG["device_prepare"]["enable_tracing_on"]),
        "hold_ms": ms.get("hold_ms", CONFIG["memstress"]["hold_ms"]),
        "launch_gap_ms": ms.get("launch_gap_ms", CONFIG["memstress"]["launch_gap_ms"]),
        "cycle_sleep_ms": ms.get("cycle_sleep_ms", CONFIG["memstress"]["cycle_sleep_ms"]),
        "burst_size": ms.get("burst_size", CONFIG["memstress"]["burst_size"]),
        "seed": ms.get("seed", CONFIG["memstress"]["seed"]),
        "clear_logcat": ms.get("clear_logcat", CONFIG["memstress"]["clear_logcat"]),
        "mode": ms.get("mode", CONFIG["memstress"]["mode"]),
        "buddyinfo_interval_s": cfg.get("buddyinfo_interval_s", CONFIG["buddyinfo_interval_s"]),
        "vmstat_interval_s": cfg.get("vmstat_interval_s", CONFIG["vmstat_interval_s"]),
        "packages": ms.get("packages", []),
    }


# --------------- main per-device run ---------------

def run_one_device(serial: str, out_dir: Path, packages: List[str],
                   args: argparse.Namespace, stop_event: threading.Event) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    memstress_out = out_dir / "memstress"
    memstress_out.mkdir(parents=True, exist_ok=True)
    for stale_name in ("launch_failures.txt", "cycle_log.jsonl", "cycle_timing.json", "cycle_timing.md"):
        stale_path = memstress_out / stale_name
        if stale_path.exists():
            stale_path.unlink()

    use_su = bool(args.use_su)
    stats_dir = args.stats_dir or auto_detect_stats_dir(serial, use_su)
    counters = [c.strip() for c in str(args.counters).split(",") if c.strip()] or list(CONFIG["counters"])
    interval_s = max(1, int(args.interval_s))

    manifest = {
        "serial": serial,
        "start_host_ts": int(time.time()),
        "status": "running",
        "config": {"stats_dir": stats_dir, "counters": counters,
                   "interval_s": interval_s, "use_su": use_su,
                   "no_network_check": bool(args.no_network_check),
                   "max_cycles": int(args.max_cycles),
                   "device_prepare": bool(args.device_prepare),
                   "enable_tracing_on": bool(args.enable_tracing_on),
                   "memstress": {"packages": packages, "burst_size": int(args.burst_size),
                                  "hold_ms": int(args.hold_ms), "launch_gap_ms": int(args.launch_gap_ms),
                                  "cycle_sleep_ms": int(args.cycle_sleep_ms),
                                  "seed": int(args.seed), "mode": str(args.mode),
                                  "clear_logcat": bool(args.clear_logcat)},
                   "buddyinfo_interval_s": int(args.buddyinfo_interval_s),
                   "vmstat_interval_s": int(args.vmstat_interval_s)},
    }
    write_run_manifest(out_dir / "run_manifest.json", manifest)

    # --- network ---
    if not args.no_network_check:
        ensure_network(serial)

    # --- device prepare ---
    if args.device_prepare:
        ensure_awake_unlocked_and_stay_awake(
            serial,
            out_dir=out_dir,
            retries=3,
            retry_sleep_s=2,
            enable_tracing_on=bool(args.enable_tracing_on),
        )

    # --- THP ensure ---
    if not args.no_thp_ensure:
        ensure_thp_mode_for_stats(serial, stats_dir=stats_dir, desired_mode="always",
                                  use_su=use_su, retries=3, retry_sleep_s=2,
                                  log_path=out_dir / "thp_ensure_log.txt")

    # --- post-prepare hook (e.g. set compaction sysctl after cool-down + lock-freq) ---
    post_cmd = getattr(args, 'post_prepare_cmd', None)
    if post_cmd:
        print(f"[{serial}] post-prepare: {post_cmd}")
        subprocess.run(
            ["adb", "-s", serial, "shell", f"su -c '{post_cmd}'"],
            capture_output=True, text=True, timeout=30)

    # --- vmstat baseline snapshot (before workload) ---
    vmstat_start = record_vmstat_start(serial, out_dir, use_su)

    # --- package resolution ---
    valid_pkgs = validate_packages(serial, packages)
    if not valid_pkgs:
        raise RuntimeError("none of the requested packages are installed")
    skipped = [p for p in packages if p not in set(valid_pkgs)]
    if skipped:
        print(f"[{serial}] skipped (not installed): {skipped}")

    resolved: Dict[str, str] = {}
    for pkg in valid_pkgs:
        comp = resolve_activity(serial, pkg)
        if comp:
            resolved[pkg] = comp
        else:
            print(f"[{serial}] could not resolve activity for {pkg}")

    if not resolved:
        raise RuntimeError("no launchable activities found")

    manifest["packages_resolved"] = resolved
    write_run_manifest(out_dir / "run_manifest.json", manifest)

    (memstress_out / "resolved_activities.json").write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    start_meminfo = subprocess.run(
        ["adb", "-s", serial, "shell", "dumpsys", "meminfo"],
        capture_output=True, text=True, timeout=60)
    (memstress_out / "dumpsys_meminfo_start.txt").write_text(start_meminfo.stdout or "", encoding="utf-8")

    # --- sampling threads ---
    local_stop = threading.Event()
    combined_stop = StopEvent()
    combined_stop.add(stop_event)
    combined_stop.add(local_stop)

    sampling_result = {"samples": 0, "errors": 0}

    def _sampler():
        try:
            n, nerr = sample_loop(
                serial=serial, stats_dir=stats_dir, counters=counters,
                use_su=use_su, interval_s=interval_s,
                out_csv=out_dir / "raw_samples.csv",
                retries=2, retry_sleep_s=2,
                stop_event=combined_stop)
            sampling_result["samples"] = n
            sampling_result["errors"] = nerr
        except Exception as e:
            print(f"[{serial}] sampler error: {e}", file=sys.stderr)
            combined_stop.set()

    sampler_thread = threading.Thread(target=_sampler, name=f"sampler_{serial}", daemon=True)
    sampler_thread.start()

    # buddyinfo
    if int(args.buddyinfo_interval_s) > 0:
        threading.Thread(
            target=buddyinfo_sample_loop,
            kwargs={"serial": serial, "out_csv": out_dir / "buddyinfo_samples.csv",
                    "interval_s": int(args.buddyinfo_interval_s), "use_su": use_su,
                    "stop_event": combined_stop},
            name=f"buddyinfo_{serial}", daemon=True).start()

    # vmstat
    if int(args.vmstat_interval_s) > 0:
        threading.Thread(
            target=vmstat_sample_loop,
            kwargs={"serial": serial, "out_csv": out_dir / "vmstat_samples.csv",
                    "interval_s": int(args.vmstat_interval_s), "use_su": use_su,
                    "stop_event": combined_stop},
            name=f"vmstat_{serial}", daemon=True).start()

    # crash detection
    crash_event = threading.Event()
    if not args.no_crash_detect:
        pkgs_set = set(resolved.keys())
        detector = TargetCrashSignatureDetector(
            serial=serial, target_packages=list(pkgs_set), window_lines=500)

        def _on_logcat_line(line: str):
            if crash_event.is_set():
                return
            payload = detector.process_line(line)
            if payload is not None:
                detector.write_payload(memstress_out / "crash_signature.json", payload)
                crash_event.set()
                combined_stop.set()

        logcat_handle = start_logcat_stream(
            serial, memstress_out,
            clear_logcat=bool(args.clear_logcat),
            line_callback=_on_logcat_line,
            stop_event=stop_event)
    else:
        logcat_handle = None

    # --- main cycle loop ---
    components = list(resolved.values())
    max_cycles = int(args.max_cycles)
    seed = int(args.seed)
    hold_ms = int(args.hold_ms)
    launch_gap_ms = int(args.launch_gap_ms)
    cycle_sleep_ms = int(args.cycle_sleep_ms)
    burst_size = max(1, int(args.burst_size))
    mode = str(args.mode)

    rng = random.Random(seed)
    cycle_log_f = (memstress_out / "cycle_log.jsonl").open("w", encoding="utf-8")
    cycle_start_ts: List[float] = []  # per-cycle wall-clock entry timestamps
    launch_failures: List[str] = []

    try:
        for cycle in range(1, max_cycles + 1):
            if stop_event.is_set():
                break

            cycle_start_ts.append(time.time())

            # shuf order for this cycle
            order = list(components)
            rng.shuffle(order)

            launched: List[str] = []
            errors: List[str] = []
            for i, comp in enumerate(order[:burst_size]):
                if stop_event.is_set():
                    break
                try:
                    launch_and_background(serial, comp, hold_ms, mode)
                    launched.append(comp)
                except Exception as e:
                    errors.append(f"{comp}:{e}")
                if i < burst_size - 1:
                    time.sleep(max(0, launch_gap_ms) / 1000.0)

            cycle_row = {"cycle": cycle, "launched": launched, "errors": errors,
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            cycle_log_f.write(json.dumps(cycle_row, ensure_ascii=False) + "\n")
            cycle_log_f.flush()

            if errors or not launched:
                launch_failures.extend(errors or ["no components launched"])
                print(f"[{serial}] launch gate failed at cycle {cycle}: launched={len(launched)} errors={len(errors)}", file=sys.stderr)
                break

            if cycle % 10 == 0:
                print(f"[{serial}] cycle {cycle}/{max_cycles} launched={len(launched)}")

            if cycle < max_cycles:
                time.sleep(max(0, cycle_sleep_ms) / 1000.0)

    finally:
        cycle_log_f.close()
        local_stop.set()

    # --- cycle timing stats ---
    if len(cycle_start_ts) >= 2:
        deltas = [cycle_start_ts[i+1] - cycle_start_ts[i] for i in range(len(cycle_start_ts)-1)]
        deltas_sorted = sorted(deltas)
        n = len(deltas_sorted)
        total_s = cycle_start_ts[-1] - cycle_start_ts[0]
        timing = {
            "total_cycles": len(cycle_start_ts),
            "total_elapsed_s": round(total_s, 3),
            "total_elapsed_ms": round(total_s * 1000, 1),
            "max_cycle_s": round(max(deltas), 3),
            "min_cycle_s": round(min(deltas), 3),
            "mean_cycle_s": round(sum(deltas) / n, 3),
            "median_cycle_s": round(deltas_sorted[n // 2], 3),
            "p90_cycle_s": round(deltas_sorted[int(n * 0.90)], 3),
            "p95_cycle_s": round(deltas_sorted[int(n * 0.95)], 3),
            "deltas_s": [round(x, 3) for x in deltas],
            "unit": "seconds",
            "note": "delta between consecutive cycle_start_ts (includes burst + json_write + cycle_sleep)",
        }
        (memstress_out / "cycle_timing.json").write_text(
            json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (memstress_out / "cycle_timing.md").write_text(
            "\n".join([
                "# cycle timing (per-cycle wall-clock)\n",
                f"- cycles: {timing['total_cycles']}",
                f"- total: {timing['total_elapsed_s']} s ({timing['total_elapsed_ms']} ms)",
                f"- mean: {timing['mean_cycle_s']} s",
                f"- max: {timing['max_cycle_s']} s",
                f"- p90: {timing['p90_cycle_s']} s",
                f"- p95: {timing['p95_cycle_s']} s",
            ]) + "\n", encoding="utf-8")

    # --- post-run ---
    if logcat_handle:
        logcat_handle.stop()

    sampler_thread.join(timeout=10)

    # --- kill fragmem if it was started ---
    if getattr(args, 'precondition', False):
        try:
            stop_fragmem(serial, use_su=use_su)
        except Exception:
            pass

    # --- post-workload hook ---
    post_wl_cmd = getattr(args, 'post_workload_cmd', None)
    if post_wl_cmd:
        print(f"[{serial}] post-workload: {post_wl_cmd}")
        subprocess.run(
            ["adb", "-s", serial, "shell", f"su -c '{post_wl_cmd}'"],
            capture_output=True, text=True, timeout=30)

    # --- vmstat final snapshot (after workload) ---
    record_vmstat_end(serial, out_dir, use_su)

    # derive metrics
    run_derive_metrics(
        scripts_dir=Path(__file__).resolve().parent,
        out_dir=out_dir,
        vmstat_start=out_dir / "vmstat_start.json",
        vmstat_end=out_dir / "vmstat_end.json",
    )
    vmstat_samples = out_dir / "vmstat_samples.csv"
    if vmstat_samples.exists():
        derive_vmstat_csv(vmstat_samples, out_dir / "vmstat_derived.csv")

    if launch_failures:
        (memstress_out / "launch_failures.txt").write_text(
            "\n".join(launch_failures) + "\n", encoding="utf-8")
        manifest["status"] = "failed_launch_gate"
        manifest["end_host_ts"] = int(time.time())
        manifest["samples"] = sampling_result["samples"]
        manifest["sample_errors"] = sampling_result["errors"]
        manifest["launch_failures"] = launch_failures[:20]
        write_run_manifest(out_dir / "run_manifest.json", manifest)
        raise RuntimeError(f"launch gate failed: {launch_failures[0]}")

    manifest["status"] = "finished"
    manifest["end_host_ts"] = int(time.time())
    manifest["samples"] = sampling_result["samples"]
    manifest["sample_errors"] = sampling_result["errors"]
    write_run_manifest(out_dir / "run_manifest.json", manifest)

    return manifest


# --------------- main ---------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Slim THP anon fallback sampler + memstress")

    p.add_argument("--serial", required=True, help="Target device serial")
    p.add_argument("--out-dir", "--out", dest="out_dir", default=None, help="Output directory")
    p.add_argument("--max-cycles", type=int, default=CONFIG["max_cycles"])
    p.add_argument("--interval-s", type=int, default=CONFIG["interval_s"])
    p.add_argument("--stats-dir", default=CONFIG["stats_dir"], help="Auto-detected if omitted")
    p.add_argument("--counters", default=",".join(CONFIG["counters"]))
    p.add_argument("--use-su", action=argparse.BooleanOptionalAction, default=CONFIG["use_su"],
                   help="Wrap root commands with su -c (default: True). Use --no-use-su if adb root is already enabled.")
    p.add_argument("--package", action="append", default=None, help="Target package (repeatable)")
    p.add_argument("--package-file", default=None, help="File with one package per line")
    p.add_argument("--burst-size", type=int, default=CONFIG["memstress"]["burst_size"])
    p.add_argument("--hold-ms", type=int, default=CONFIG["memstress"]["hold_ms"])
    p.add_argument("--launch-gap-ms", type=int, default=CONFIG["memstress"]["launch_gap_ms"])
    p.add_argument("--cycle-sleep-ms", type=int, default=CONFIG["memstress"]["cycle_sleep_ms"])
    p.add_argument("--seed", type=int, default=CONFIG["memstress"]["seed"])
    p.add_argument("--mode", choices=["launch_only", "interactive"], default=CONFIG["memstress"]["mode"])
    p.add_argument("--clear-logcat", "--no-clear-logcat", dest="clear_logcat",
                   action=argparse.BooleanOptionalAction, default=CONFIG["memstress"]["clear_logcat"])
    p.add_argument("--no-network-check", action="store_true", default=CONFIG["no_network_check"],
                   help="Skip network connectivity check (default: skip)")
    p.add_argument("--no-crash-detect", action="store_true", help="Disable crash detection and logcat streaming")
    p.add_argument("--no-thp-ensure", action="store_true", help="Skip THP enabled ensure check/write")
    p.add_argument("--buddyinfo-interval-s", type=int, default=CONFIG["buddyinfo_interval_s"])
    p.add_argument("--vmstat-interval-s", type=int, default=CONFIG["vmstat_interval_s"])
    p.add_argument(
        "--device-prepare",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["device_prepare"]["enabled"],
        help="Wake/unlock/keep screen on before workload (default: on)",
    )
    p.add_argument(
        "--enable-tracing-on",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["device_prepare"]["enable_tracing_on"],
        help="During device prepare, write 1 to /sys/kernel/tracing/tracing_on (default: on). No events are enabled, so overhead is near zero.",
    )
    p.add_argument("--from-manifest", default=None, help="Load all params from a previous run_manifest.json")
    p.add_argument("--post-prepare-cmd", default=None,
                   help="Shell command to run on device (via su) after device-prepare but before workload. "
                        "Use for setting compaction sysctl after cool-down.")
    p.add_argument("--post-workload-cmd", default=None,
                   help="Shell command to run on device (via su) after workload ends but before vmstat_end. "
                        "Use for stopping simpleperf etc.")

    # Preconditioning options (fragmem-based)
    p.add_argument("--precondition", action="store_true", default=False,
                   help="Run fragmem preconditioning to fragment memory before memstress")
    p.add_argument("--precondition-threshold", type=int, default=2000,
                   help="Buddyinfo sum(order>=2) threshold for fragmem (default: 2000)")
    p.add_argument("--precondition-alloc-mb", type=int, default=4000,
                   help="Total MB for fragmem to allocate (default: 5000)")

    args = p.parse_args(argv)

    if args.from_manifest:
        mani_args = load_manifest_args(args.from_manifest)
        for k, v in mani_args.items():
            if k == "packages":
                if not args.package:
                    args.package = v
            elif k == "counters" and not args.counters:
                setattr(args, "counters", v)
            elif getattr(args, k, None) == p.get_default(k):
                setattr(args, k, v)

    return args


def ensure_out_dir(out_dir: Optional[str], default_prefix: str = "thp_memstress") -> Path:
    if out_dir:
        p = Path(out_dir)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        p = Path(f"/tmp/{default_prefix}_{ts}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()
    out_dir = ensure_out_dir(args.out_dir)

    packages: List[str] = []
    if args.package:
        packages.extend(args.package)
    if args.package_file:
        packages.extend(read_package_file(args.package_file))
    packages = list(dict.fromkeys(p for p in packages if p))

    if not packages:
        print("[error] no packages specified. Use --package or --package-file.", file=sys.stderr)
        return 1

    stop_event = threading.Event()

    def _handler(sig, frame):
        print("\n[stopping]")
        stop_event.set()
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    manifest = run_one_device(args.serial, out_dir, packages, args, stop_event)
    print(f"[{manifest['serial']}] done. out_dir={out_dir} "
          f"samples={manifest.get('samples', 0)} errors={manifest.get('sample_errors', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
