#!/usr/bin/env python3
"""Run THP anon fallback sampling + a memory-heavy Android app launch/kill workload via adb.

The workload is host-side:
- repeatedly launches a burst of apps
- keeps several apps alive to grow resident memory pressure
- force-stops older apps to keep the cycle moving
- biases heavy apps such as camera/video/media packages

Outputs (under --out-dir):
- raw_samples.csv / derived.csv / summary.md (THP sampling + derived metrics)
- memstress/ (logcat + cycle log + dumpsys snapshots)
- run_manifest.json
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Set, Tuple

from utils.adb_utils import adb_shell_cp, ensure_adb_works, resolve_serials
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
    "setup_shell": [],
    "apk_dir": None,
    "thp_ensure": {
        "enabled": True,
        "desired_mode": "always",
        "retries": 3,
        "retry_sleep_s": 2,
    },
    "device_prepare": {
        "enabled": True,
        "retries": 3,
        "retry_sleep_s": 2,
    },
    "memstress": {
        "packages": [],
        "package_file": None,
        "heavy_packages": [],
        "heavy_package_file": None,
        "prefer_keywords": "camera,video,recorder,player,gallery,photo,media,stream",
        "burst_size": 4,
        "heavy_per_burst": 2,
        "max_alive": 8,
        # Default dwell after launching a package before we "exit" it (HOME) or evict older apps.
        # User-requested flash behavior: ~200ms.
        "hold_ms": 200,
        "launch_gap_ms": 350,
        "cycle_sleep_ms": 1000,
        "seed": 12345,
        "clear_logcat": True,
        # Workload behavior toggles (kept as parameters, not a separate policy enum):
        # - am_start_wait=False: use `am start` (no -W) to avoid fully waiting activity startup.
        # - post_launch_action=home: after hold_ms, press HOME to exit foreground but keep process cached.
        # - force_stop_evict=False: do not force-stop packages (leave cleanup to LMKD / natural pressure).
        "am_start_wait": False,
        "post_launch_action": "home",
        "force_stop_evict": False,
    },
    "sample_retries": 2,
    "sample_retry_sleep_s": 2,
}


def install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def validate_packages(serial: str, pkgs: Sequence[str]) -> List[str]:
    """Return subset of pkgs that are installed on device.

    Uses a single `pm list packages` call (much faster than N x `pm path`).
    """

    cp = adb_shell_cp(serial, "pm list packages", timeout_s=60, check=False)
    installed: Set[str] = set()
    for line in (cp.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            installed.add(line[len("package:"):].strip())

    return [p for p in pkgs if p in installed]


def resolve_activity(serial: str, pkg: str) -> Optional[str]:
    cmds = [
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {pkg}",
        f"cmd package resolve-activity --brief {pkg}",
    ]
    for cmd in cmds:
        cp = adb_shell_cp(serial, cmd, timeout_s=20, check=False)
        if cp.returncode != 0:
            continue
        lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]
        for line in reversed(lines):
            if "/" in line and not line.startswith("priority="):
                return line
    return None


def start_activity(serial: str, component: str):
    raise NotImplementedError("use start_activity_with_mode()")


def start_activity_with_mode(serial: str, component: str, *, wait: bool):
    # `am start -W` waits for launch to complete; without `-W` it returns much sooner.
    cmd = f"am start {'-W ' if wait else ''}-n {component}".strip()
    return adb_shell_cp(serial, cmd, timeout_s=45, check=False)


def exit_to_home(serial: str):
    # Prefer input keyevent (fast, doesn't depend on resolving HOME intent).
    cp = adb_shell_cp(serial, "input keyevent KEYCODE_HOME", timeout_s=10, check=False)
    if cp.returncode == 0:
        return cp
    # Fallback: explicitly start HOME.
    return adb_shell_cp(serial, "am start -a android.intent.action.MAIN -c android.intent.category.HOME", timeout_s=20, check=False)


def force_stop(serial: str, pkg: str):
    return adb_shell_cp(serial, f"am force-stop {pkg}", timeout_s=20, check=False)


def maybe_sleep(ms: int, deadline: Optional[float]) -> None:
    if ms <= 0:
        return
    remaining = ms / 1000.0
    if deadline is not None:
        remaining = min(remaining, max(0.0, deadline - time.time()))
    if remaining > 0:
        time.sleep(remaining)


def classify_heavy_packages(pkgs: Sequence[str], explicit_heavy: Sequence[str], keywords: Sequence[str]) -> List[str]:
    explicit = set(explicit_heavy)
    out: List[str] = []
    for pkg in pkgs:
        lower = pkg.lower()
        if pkg in explicit or any(k and k in lower for k in keywords):
            out.append(pkg)
    return out


def take_from_pool(pool: Deque[str], count: int, banned: Set[str]) -> List[str]:
    if count <= 0 or not pool:
        return []
    picked: List[str] = []
    seen_this_round: Set[str] = set()
    tries = 0
    max_tries = max(len(pool) * 3, count * 3)
    while len(picked) < count and tries < max_tries and pool:
        item = pool[0]
        pool.rotate(-1)
        tries += 1
        if item in banned or item in seen_this_round:
            continue
        picked.append(item)
        seen_this_round.add(item)
    return picked


def remove_from_alive(alive: Deque[str], pkg: str) -> None:
    try:
        alive.remove(pkg)
    except ValueError:
        pass
    alive.append(pkg)


@dataclass
class SamplingResult:
    samples: int = 0
    errors: int = 0
    exc: str = ""


class CombinedEvent:
    """A tiny `threading.Event`-like helper for composing stop conditions.

    Some parts of the pipeline need a per-device stop (to stop that device's
    sampler thread) while fleet mode needs a global stop (SIGINT/SIGTERM, or
    fail-fast on error). `sample_loop()` only needs an object with `.is_set()`.
    """

    def __init__(self, *events: threading.Event):
        self._events = [e for e in events if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)

    def set(self) -> None:
        for e in self._events:
            e.set()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Android THP anon fallback sampler + memstress (adb host-side)")

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
    p.add_argument("--out-dir", "--out", dest="out_dir", default=None, help="Output directory")

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
    p.add_argument("--apk-dir", default=CONFIG["apk_dir"], help="Directory of *.apk to install before running")

    p.add_argument(
        "--thp-ensure-mode",
        default=CONFIG["thp_ensure"]["desired_mode"],
        help="Ensure this mode in <stats_dir_parent>/enabled (use 'none' to check-only)",
    )
    p.add_argument("--no-thp-ensure", action="store_true", help="Skip THP mode ensure/check workflow")
    p.add_argument("--thp-ensure-retries", type=int, default=CONFIG["thp_ensure"]["retries"])
    p.add_argument("--thp-ensure-retry-s", type=int, default=CONFIG["thp_ensure"]["retry_sleep_s"])

    p.add_argument(
        "--device-prepare",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["device_prepare"]["enabled"],
        help="Try to wake/unlock + keep screen on before starting workload",
    )
    p.add_argument("--device-prepare-retries", type=int, default=CONFIG["device_prepare"]["retries"])
    p.add_argument("--device-prepare-retry-s", type=int, default=CONFIG["device_prepare"]["retry_sleep_s"])

    # Memstress workload config.
    p.add_argument("--package", action="append", default=None, help="Target package (repeatable)")
    p.add_argument("--package-file", default=CONFIG["memstress"]["package_file"], help="File with target packages")
    p.add_argument("--heavy-package", action="append", default=None, help="Explicit heavy package (repeatable)")
    p.add_argument("--heavy-package-file", default=CONFIG["memstress"]["heavy_package_file"], help="File with explicit heavy packages")
    p.add_argument("--prefer-keywords", default=CONFIG["memstress"]["prefer_keywords"], help="Comma-separated keywords for auto-heavy classification")

    p.add_argument("--burst-size", type=int, default=CONFIG["memstress"]["burst_size"])
    p.add_argument("--heavy-per-burst", type=int, default=CONFIG["memstress"]["heavy_per_burst"])
    p.add_argument(
        "--max-alive",
        type=int,
        default=CONFIG["memstress"]["max_alive"],
        help="Keep at most N packages alive; when exceeded, kill oldest first (LRU-ish). Use 0 for start-then-kill.",
    )
    p.add_argument(
        "--hold-ms",
        type=int,
        default=CONFIG["memstress"]["hold_ms"],
        help="Hold milliseconds *after each successful launch* before post-launch action (HOME) and/or eviction.",
    )
    p.add_argument("--launch-gap-ms", type=int, default=CONFIG["memstress"]["launch_gap_ms"])
    p.add_argument("--cycle-sleep-ms", type=int, default=CONFIG["memstress"]["cycle_sleep_ms"])
    p.add_argument("--seed", type=int, default=CONFIG["memstress"]["seed"], help="Deterministic seed")
    p.add_argument(
        "--clear-logcat",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["clear_logcat"],
        help="Clear logcat before starting workload/log collection",
    )
    p.add_argument(
        "--am-start-wait",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["am_start_wait"],
        help="Use `am start -W` to wait for activity launch completion. Default: no-wait (`am start`).",
    )
    p.add_argument(
        "--post-launch-action",
        choices=["none", "home"],
        default=CONFIG["memstress"]["post_launch_action"],
        help="After hold_ms, perform an action to exit foreground without killing the process (default: home).",
    )
    p.add_argument(
        "--force-stop-evict",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["force_stop_evict"],
        help="If enabled, enforce --max-alive via `am force-stop` and force-stop all remaining at end. Default: no force-stop.",
    )

    p.add_argument("--sample-retries", type=int, default=CONFIG["sample_retries"], help="Sampling retries per tick")
    p.add_argument("--sample-retry-sleep-s", type=int, default=CONFIG["sample_retry_sleep_s"], help="Sleep seconds between sampling retries")

    return p.parse_args(argv)


def run_one_device(
    *,
    serial: str,
    out_dir: Path,
    args: argparse.Namespace,
    stop_event: threading.Event,
) -> Dict:
    scripts_dir = Path(__file__).resolve().parent
    memstress_out = out_dir / "memstress"
    memstress_out.mkdir(parents=True, exist_ok=True)

    duration_s = max(1, int(args.duration_s))
    counters = [x.strip() for x in str(args.counters).split(",") if x.strip()]
    setup_cmds = CONFIG["setup_shell"] if args.setup_shell is None else list(args.setup_shell)

    all_pkgs: List[str] = []
    if args.package:
        all_pkgs.extend(args.package)
    else:
        all_pkgs.extend(CONFIG["memstress"]["packages"])
    all_pkgs.extend(read_package_file(args.package_file))
    all_pkgs = unique_preserve_order(all_pkgs)
    if not all_pkgs:
        raise RuntimeError("memstress requires at least one --package/--package-file (or CONFIG['memstress']['packages'])")

    explicit_heavy: List[str] = []
    if args.heavy_package:
        explicit_heavy.extend(args.heavy_package)
    else:
        explicit_heavy.extend(CONFIG["memstress"]["heavy_packages"])
    explicit_heavy.extend(read_package_file(args.heavy_package_file))
    explicit_heavy = unique_preserve_order(explicit_heavy)

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
            "apk_dir": args.apk_dir,
            "thp_ensure": {
                "enabled": (not args.no_thp_ensure),
                "desired_mode": args.thp_ensure_mode,
                "retries": int(args.thp_ensure_retries),
                "retry_sleep_s": int(args.thp_ensure_retry_s),
            },
            "device_prepare": {
                "enabled": bool(args.device_prepare),
                "retries": int(args.device_prepare_retries),
                "retry_sleep_s": int(args.device_prepare_retry_s),
            },
            "memstress": {
                "packages": all_pkgs,
                "explicit_heavy_packages": explicit_heavy,
                "prefer_keywords": args.prefer_keywords,
                "burst_size": int(args.burst_size),
                "heavy_per_burst": int(args.heavy_per_burst),
                "max_alive": int(args.max_alive),
                "hold_ms": int(args.hold_ms),
                "launch_gap_ms": int(args.launch_gap_ms),
                "cycle_sleep_ms": int(args.cycle_sleep_ms),
                "seed": int(args.seed),
                "clear_logcat": bool(args.clear_logcat),
                "am_start_wait": bool(args.am_start_wait),
                "post_launch_action": str(args.post_launch_action),
                "force_stop_evict": bool(args.force_stop_evict),
            },
        },
        "samples": 0,
        "sample_errors": 0,
        "end_host_ts": None,
        "error": "",
    }
    write_run_manifest(out_dir / "run_manifest.json", manifest)

    try:
        # Per-device stop is used to stop the sampler thread when this device finishes,
        # without terminating other devices in fleet mode.
        local_stop_event = threading.Event()
        stop = CombinedEvent(stop_event, local_stop_event)

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
                out_dir=memstress_out,
                retries=int(args.device_prepare_retries),
                retry_sleep_s=int(args.device_prepare_retry_s),
            )

        valid_pkgs = validate_packages(serial, all_pkgs)
        if not valid_pkgs:
            raise RuntimeError("none of the requested memstress packages are installed on device")

        skipped_pkgs = [pkg for pkg in all_pkgs if pkg not in set(valid_pkgs)]

        resolved: Dict[str, str] = {}
        unresolved: List[str] = []
        for pkg in valid_pkgs:
            comp = resolve_activity(serial, pkg)
            if comp:
                resolved[pkg] = comp
            else:
                unresolved.append(pkg)

        runnable_pkgs = [pkg for pkg in valid_pkgs if pkg in resolved]
        if not runnable_pkgs:
            raise RuntimeError("no launchable packages resolved for memstress workload")

        keywords = [x.strip().lower() for x in str(args.prefer_keywords).split(",") if x.strip()]
        inferred_heavy = classify_heavy_packages(runnable_pkgs, explicit_heavy, keywords)

        manifest["memstress_resolved"] = {
            "packages": runnable_pkgs,
            "explicit_heavy_packages": explicit_heavy,
            "effective_heavy_packages": inferred_heavy,
            "skipped_not_installed": skipped_pkgs,
            "skipped_unresolved": unresolved,
            "resolved_activities": resolved,
        }
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        sampling_result = SamplingResult()

        def _sampler() -> None:
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
                    stop_event=stop,
                )
                sampling_result.samples = n
                sampling_result.errors = nerr
            except Exception as e:
                sampling_result.exc = str(e)
                stop.set()

        sampler_thread = threading.Thread(target=_sampler, name=f"thp_sampler_{serial}", daemon=True)
        sampler_thread.start()

        from utils.adb_utils import start_logcat

        logcat = start_logcat(serial, memstress_out, clear_logcat=bool(args.clear_logcat))

        cycle_log_f = (memstress_out / "cycle_log.jsonl").open("w", encoding="utf-8")
        try:
            (memstress_out / "resolved_activities.json").write_text(
                json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            start_meminfo = adb_shell_cp(serial, "dumpsys meminfo", timeout_s=60, check=False)
            (memstress_out / "dumpsys_meminfo_start.txt").write_text(start_meminfo.stdout or "", encoding="utf-8")

            rng = random.Random(int(args.seed))
            all_pool_list = list(runnable_pkgs)
            heavy_pool_list = [pkg for pkg in runnable_pkgs if pkg in set(inferred_heavy)]
            rng.shuffle(all_pool_list)
            rng.shuffle(heavy_pool_list)
            all_pool: Deque[str] = deque(all_pool_list)
            heavy_pool: Deque[str] = deque(heavy_pool_list)

            alive: Deque[str] = deque()
            cycle = 0
            launched_total = 0
            killed_total = 0
            deadline = time.time() + duration_s

            manifest["status"] = "running"
            write_run_manifest(out_dir / "run_manifest.json", manifest)

            while not stop.is_set():
                if time.time() >= deadline:
                    break

                cycle += 1
                chosen: List[str] = []
                chosen_set: Set[str] = set()

                heavy_target = min(int(args.heavy_per_burst), int(args.burst_size), len(heavy_pool))
                for pkg in take_from_pool(heavy_pool, heavy_target, chosen_set):
                    chosen.append(pkg)
                    chosen_set.add(pkg)

                remain = max(0, int(args.burst_size) - len(chosen))
                for pkg in take_from_pool(all_pool, remain, chosen_set):
                    chosen.append(pkg)
                    chosen_set.add(pkg)

                if not chosen:
                    break

                cycle_row = {
                    "cycle": cycle,
                    "host_ts": int(time.time()),
                    "chosen": chosen,
                    "launched": [],
                    "launch_errors": [],
                    "killed": [],
                    "alive_before_cleanup": list(alive),
                }

                max_alive = max(0, int(args.max_alive))

                for pkg in chosen:
                    if stop.is_set() or time.time() >= deadline:
                        break
                    component = resolved[pkg]
                    cp = start_activity_with_mode(serial, component, wait=bool(args.am_start_wait))
                    stdout_text = (cp.stdout or "")
                    ok = cp.returncode == 0 and "Error:" not in stdout_text and "Exception occurred" not in stdout_text
                    if ok:
                        remove_from_alive(alive, pkg)
                        cycle_row["launched"].append(pkg)
                        launched_total += 1

                        # Keep the just-launched app in foreground for a short dwell time.
                        maybe_sleep(int(args.hold_ms), deadline)

                        # Exit foreground without killing: default is HOME.
                        if str(args.post_launch_action) == "home":
                            exit_to_home(serial)

                        # Optional eviction (old behavior): enforce max-alive via force-stop.
                        if bool(args.force_stop_evict):
                            while len(alive) > max_alive:
                                victim = alive.popleft()
                                force_stop(serial, victim)
                                cycle_row["killed"].append(victim)
                                killed_total += 1
                    else:
                        cycle_row["launch_errors"].append({
                            "package": pkg,
                            "returncode": cp.returncode,
                            "stderr": (cp.stderr or "").strip(),
                            "stdout_tail": stdout_text.strip()[-300:],
                        })
                    maybe_sleep(int(args.launch_gap_ms), deadline)

                cycle_row["alive_after_cleanup"] = list(alive)
                cycle_log_f.write(json.dumps(cycle_row, ensure_ascii=False) + "\n")
                cycle_log_f.flush()
                print(
                    f"[{serial}][memstress] cycle={cycle} launched={len(cycle_row['launched'])} "
                    f"killed={len(cycle_row['killed'])} alive={len(alive)}"
                )

                maybe_sleep(int(args.cycle_sleep_ms), deadline)

            cleanup_killed: List[str] = []
            if bool(args.force_stop_evict):
                while alive:
                    victim = alive.popleft()
                    force_stop(serial, victim)
                    cleanup_killed.append(victim)
                    killed_total += 1

            end_meminfo = adb_shell_cp(serial, "dumpsys meminfo", timeout_s=60, check=False)
            (memstress_out / "dumpsys_meminfo_end.txt").write_text(end_meminfo.stdout or "", encoding="utf-8")

            summary = {
                "serial": serial,
                "cycles": cycle,
                "launched_total": launched_total,
                "killed_total": killed_total,
                "runnable_packages": len(runnable_pkgs),
                "heavy_packages": inferred_heavy,
                "cleanup_killed": cleanup_killed,
                "stopped_by_signal": stop_event.is_set(),
            }
            (memstress_out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False))
        finally:
            try:
                cycle_log_f.close()
            except Exception:
                pass
            try:
                logcat.stop()
            except Exception:
                pass

        # Stop this device's sampler thread (do NOT stop other devices).
        local_stop_event.set()
        sampler_thread.join(timeout=30)
        if sampling_result.exc:
            raise RuntimeError(f"sampling thread failed: {sampling_result.exc}")

        manifest["samples"] = sampling_result.samples
        manifest["sample_errors"] = sampling_result.errors
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        run_derive_metrics(scripts_dir=scripts_dir, out_dir=out_dir)

        manifest["status"] = "finished" if not stop_event.is_set() else "stopped"
        manifest["end_host_ts"] = int(time.time())
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        return {
            "serial": serial,
            "out_dir": str(out_dir),
            "samples": sampling_result.samples,
            "sample_errors": sampling_result.errors,
        }
    except Exception as e:
        manifest["status"] = "failed"
        manifest["end_host_ts"] = int(time.time())
        manifest["error"] = str(e)
        write_run_manifest(out_dir / "run_manifest.json", manifest)
        raise


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()

    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    serials = resolve_serials(args.serial, all_devices=bool(args.all_devices))
    multi = len(serials) > 1

    top_out_dir = ensure_out_dir(args.out_dir, default_prefix="thp_memstress")
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
            print(f"[{v['serial']}] out_dir={v['out_dir']} samples={v['samples']} errors={v['sample_errors']}")
        else:
            print(f"[{r.name}] FAIL: {r.error}", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
