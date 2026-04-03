#!/usr/bin/env python3
"""Run a long-lived memory-heavy Android app launch/kill workload via adb.

The workload is host-side and intentionally separate from monkey:
- repeatedly launches a burst of apps
- keeps several apps alive to grow resident memory pressure
- force-stops older apps to keep the cycle moving
- biases heavy apps such as camera/video/media packages
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Set, Tuple


STOP_REQUESTED = False


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run(cmd: List[str], timeout_s: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=check)


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


def adb_shell(serial: str, cmd: str, timeout_s: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    return _run(adb_base(serial) + ["shell", cmd], timeout_s=timeout_s, check=check)


def read_package_file(path_str: Optional[str]) -> List[str]:
    if not path_str:
        return []
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"package file not found: {path}")
    pkgs: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        x = line.strip()
        if not x or x.startswith("#"):
            continue
        pkgs.append(x)
    return pkgs


def unique_preserve_order(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(items))


def install_signal_handlers() -> None:
    def _handle(_signum, _frame) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def validate_packages(serial: str, pkgs: Sequence[str]) -> List[str]:
    ok: List[str] = []
    for pkg in pkgs:
        cp = adb_shell(serial, f"pm path {pkg}", timeout_s=20, check=False)
        if cp.returncode == 0 and cp.stdout.strip().startswith("package:"):
            ok.append(pkg)
    return ok


def resolve_activity(serial: str, pkg: str) -> Optional[str]:
    cmds = [
        f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {pkg}",
        f"cmd package resolve-activity --brief {pkg}",
    ]
    for cmd in cmds:
        cp = adb_shell(serial, cmd, timeout_s=20, check=False)
        if cp.returncode != 0:
            continue
        lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
        for line in reversed(lines):
            if "/" in line and not line.startswith("priority="):
                return line
    return None


def start_activity(serial: str, component: str) -> subprocess.CompletedProcess:
    return adb_shell(serial, f"am start -W -n {component}", timeout_s=45, check=False)


def force_stop(serial: str, pkg: str) -> subprocess.CompletedProcess:
    return adb_shell(serial, f"am force-stop {pkg}", timeout_s=20, check=False)


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


def start_logcat(serial: str, out_dir: Path, clear_logcat: bool) -> Tuple[subprocess.Popen, Path]:
    logcat_path = out_dir / "logcat_all_threadtime.txt"
    base = adb_base(serial)
    if clear_logcat:
        _run(base + ["logcat", "-c"], timeout_s=20, check=False)
    fh = logcat_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(base + ["logcat", "-v", "threadtime", "-b", "all"], stdout=fh, stderr=subprocess.DEVNULL)
    return proc, logcat_path


def stop_logcat(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Memory-heavy launch/kill Android app cycle via adb")
    p.add_argument("--serial", default=None, help="Target device serial")
    p.add_argument("--package", action="append", default=[], help="Target package (repeatable)")
    p.add_argument("--package-file", default=None, help="File with target packages")
    p.add_argument("--heavy-package", action="append", default=[], help="Explicit heavy package (repeatable)")
    p.add_argument("--heavy-package-file", default=None, help="File with explicit heavy packages")
    p.add_argument("--prefer-keywords", default="camera,video,recorder,player,gallery,photo,media,stream",
                   help="Comma-separated keywords for auto-heavy classification")
    p.add_argument("--duration-s", type=int, default=0, help="How long to run; 0 means run until killed")
    p.add_argument("--burst-size", type=int, default=4, help="How many apps to launch per burst")
    p.add_argument("--heavy-per-burst", type=int, default=2, help="Preferred heavy apps per burst")
    p.add_argument("--max-alive", type=int, default=8, help="Maximum launched apps kept alive before cleanup")
    p.add_argument("--hold-ms", type=int, default=5000, help="Hold time after each burst before cleanup")
    p.add_argument("--launch-gap-ms", type=int, default=350, help="Gap between launches inside one burst")
    p.add_argument("--cycle-sleep-ms", type=int, default=1000, help="Gap between bursts")
    p.add_argument("--seed", type=int, default=None, help="Deterministic seed")
    p.add_argument("--out", default=None, help="Output directory")
    p.add_argument("--clear-logcat", action="store_true", help="Clear logcat before starting")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    install_signal_handlers()
    args = parse_args(argv)

    try:
        _run(["adb", "version"], timeout_s=10, check=True)
    except Exception:
        print("ERROR: adb not found or not working in PATH", file=sys.stderr)
        return 2

    serial = resolve_serial(args.serial)
    out_dir = Path(args.out) if args.out else Path("memstress_logs") / f"{serial}_{_now_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_pkgs = list(args.package)
    all_pkgs.extend(read_package_file(args.package_file))
    all_pkgs = unique_preserve_order(all_pkgs)
    if not all_pkgs:
        raise RuntimeError("memstress requires at least one --package or --package-file entry")

    explicit_heavy = list(args.heavy_package)
    explicit_heavy.extend(read_package_file(args.heavy_package_file))
    explicit_heavy = unique_preserve_order(explicit_heavy)

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

    keywords = [x.strip().lower() for x in args.prefer_keywords.split(",") if x.strip()]
    inferred_heavy = classify_heavy_packages(runnable_pkgs, explicit_heavy, keywords)

    rng = random.Random(args.seed)
    all_pool_list = list(runnable_pkgs)
    heavy_pool_list = [pkg for pkg in runnable_pkgs if pkg in set(inferred_heavy)]
    rng.shuffle(all_pool_list)
    rng.shuffle(heavy_pool_list)
    all_pool: Deque[str] = deque(all_pool_list)
    heavy_pool: Deque[str] = deque(heavy_pool_list)

    logcat_proc, _ = start_logcat(serial, out_dir, args.clear_logcat)
    cycle_log = (out_dir / "cycle_log.jsonl").open("w", encoding="utf-8")

    try:
        manifest = {
            "serial": serial,
            "start_host_ts": int(time.time()),
            "config": {
                "duration_s": args.duration_s,
                "burst_size": args.burst_size,
                "heavy_per_burst": args.heavy_per_burst,
                "max_alive": args.max_alive,
                "hold_ms": args.hold_ms,
                "launch_gap_ms": args.launch_gap_ms,
                "cycle_sleep_ms": args.cycle_sleep_ms,
                "prefer_keywords": keywords,
                "seed": args.seed,
            },
            "packages": runnable_pkgs,
            "explicit_heavy_packages": explicit_heavy,
            "effective_heavy_packages": inferred_heavy,
            "skipped_not_installed": skipped_pkgs,
            "skipped_unresolved": unresolved,
            "resolved_activities": resolved,
        }
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        start_meminfo = adb_shell(serial, "dumpsys meminfo", timeout_s=60, check=False)
        (out_dir / "dumpsys_meminfo_start.txt").write_text(start_meminfo.stdout, encoding="utf-8")

        alive: Deque[str] = deque()
        cycle = 0
        launched_total = 0
        killed_total = 0
        deadline = time.time() + args.duration_s if args.duration_s > 0 else None

        while not STOP_REQUESTED:
            if deadline is not None and time.time() >= deadline:
                break

            cycle += 1
            chosen: List[str] = []
            chosen_set: Set[str] = set()

            heavy_target = min(args.heavy_per_burst, args.burst_size, len(heavy_pool))
            for pkg in take_from_pool(heavy_pool, heavy_target, chosen_set):
                chosen.append(pkg)
                chosen_set.add(pkg)

            remain = max(0, args.burst_size - len(chosen))
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

            for pkg in chosen:
                if STOP_REQUESTED:
                    break
                if deadline is not None and time.time() >= deadline:
                    break
                component = resolved[pkg]
                cp = start_activity(serial, component)
                stdout_text = cp.stdout or ""
                ok = cp.returncode == 0 and "Error:" not in stdout_text and "Exception occurred" not in stdout_text
                if ok:
                    remove_from_alive(alive, pkg)
                    cycle_row["launched"].append(pkg)
                    launched_total += 1
                else:
                    cycle_row["launch_errors"].append({
                        "package": pkg,
                        "returncode": cp.returncode,
                        "stderr": (cp.stderr or "").strip(),
                        "stdout_tail": stdout_text.strip()[-300:],
                    })
                maybe_sleep(args.launch_gap_ms, deadline)

            maybe_sleep(args.hold_ms, deadline)

            while len(alive) > max(1, args.max_alive):
                victim = alive.popleft()
                force_stop(serial, victim)
                cycle_row["killed"].append(victim)
                killed_total += 1

            cycle_row["alive_after_cleanup"] = list(alive)
            cycle_log.write(json.dumps(cycle_row, ensure_ascii=False) + "\n")
            cycle_log.flush()
            print(
                f"[memstress] cycle={cycle} launched={len(cycle_row['launched'])} "
                f"killed={len(cycle_row['killed'])} alive={len(alive)}"
            )

            maybe_sleep(args.cycle_sleep_ms, deadline)

        # Cleanup remaining apps at end for a clean device state.
        cleanup_killed: List[str] = []
        while alive:
            victim = alive.popleft()
            force_stop(serial, victim)
            cleanup_killed.append(victim)
            killed_total += 1

        end_meminfo = adb_shell(serial, "dumpsys meminfo", timeout_s=60, check=False)
        (out_dir / "dumpsys_meminfo_end.txt").write_text(end_meminfo.stdout, encoding="utf-8")

        summary = {
            "serial": serial,
            "cycles": cycle,
            "launched_total": launched_total,
            "killed_total": killed_total,
            "runnable_packages": len(runnable_pkgs),
            "heavy_packages": inferred_heavy,
            "cleanup_killed": cleanup_killed,
            "stopped_by_signal": STOP_REQUESTED,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        cycle_log.close()
        stop_logcat(logcat_proc)


if __name__ == "__main__":
    raise SystemExit(main())
