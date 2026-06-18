#!/usr/bin/env python3
"""Run THP anon fallback sampling + a memory-heavy Android app launch workload via adb.

The workload is host-side:
- repeatedly launches a burst of apps
- after each launch, waits briefly then presses HOME (exit foreground without killing)
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

from utils.adb_utils import adb_shell, adb_shell_cp, ensure_adb_works, resolve_serials
from utils.crash_signature import TargetCrashSignatureDetector
from utils.device_prep import ensure_awake_unlocked_and_stay_awake
from utils.experiment_utils import ensure_out_dir, maybe_install_apks, run_setup_cmds
from utils.interactive import interactive_click_loop
from utils.oat_watch import DEFAULT_DELETE_EXTS, resolve_oat_watch_packages, watch_loop
from utils.pkg_utils import read_package_file, unique_preserve_order
from utils.sampling_utils import DEFAULT_COUNTERS, DEFAULT_STATS_DIR, run_derive_metrics, sample_loop, write_run_manifest
from utils.thp_utils import ensure_thp_mode_for_stats
from utils.buddyinfo_utils import buddyinfo_sample_loop, buddyinfo_with_thp_sample_loop
from utils.task_pool import TaskPool
from utils.vmstat_utils import derive_vmstat_csv, vmstat_sample_loop


# === CONFIG (edit here) ===
CONFIG = {
    "max_cycles": 1200,
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
        # Default dwell after launching a package before we "exit" it (HOME) or evict older apps.
        # User-requested flash behavior: ~200ms.
        "hold_ms": 200,
        "launch_gap_ms": 350,
        "cycle_sleep_ms": 1000,
        # Optional: treat cycles as "rounds", and force-stop all target packages at each round boundary.
        # 0 disables this mode.
        "round_s": 0,
        "selection_mode": "epoch",
        "epoch_reshuffle": True,
        "victim_packages": [],
        "victim_exclude_from_churn": True,
        "victim_prime_hold_ms": 3000,
        "victim_revisit_every_cycles": 0,
        "victim_revisit_hold_ms": 1500,
        "seed": 12345,
        "clear_logcat": True,
    },
    "interactive": {
        "mode": False,
    },
    "sample_retries": 2,
    "sample_retry_sleep_s": 2,
    "buddyinfo_interval_s": 5,
    "vmstat_interval_s": 60,
    "readahead_min_order": None,
    "ext4_folio_order": None,
    "f2fs_max_folio_order": None,
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
    # Intentionally do NOT use `-W`: avoid fully waiting for activity launch completion.
    return adb_shell_cp(serial, f"am start -n {component}", timeout_s=20, check=False)


def exit_to_home(serial: str):
    cp = adb_shell_cp(serial, "input keyevent KEYCODE_HOME", timeout_s=10, check=False)
    if cp.returncode == 0:
        return cp
    return adb_shell_cp(serial, "am start -a android.intent.action.MAIN -c android.intent.category.HOME", timeout_s=20, check=False)


def write_trace_marker(serial: str, message: str):
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    cmd = (
        "su -c "
        f"\"sh -c 'echo \\\"{safe}\\\" > /sys/kernel/debug/tracing/trace_marker'\""
    )
    return adb_shell_cp(serial, cmd, timeout_s=10, check=False)


def force_stop_packages(serial: str, pkgs: Sequence[str]) -> List[Dict]:
    """Best-effort `am force-stop` for each pkg; returns per-pkg results for logging."""

    results: List[Dict] = []
    for pkg in pkgs:
        cp = adb_shell_cp(serial, f"am force-stop {pkg}", timeout_s=20, check=False)
        results.append(
            {
                "package": pkg,
                "returncode": cp.returncode,
                "stdout_tail": (cp.stdout or "").strip()[-200:],
                "stderr_tail": (cp.stderr or "").strip()[-200:],
            }
        )
    return results


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


@dataclass
class EpochPackagePool:
    """Pick packages without replacement inside one epoch.

    The pool is shuffled at epoch boundaries, then consumed in order.
    This makes the no-replacement behavior explicit instead of relying on a
    rotating deque side effect.
    """

    items: Sequence[str]
    seed: int
    reshuffle_each_epoch: bool = True

    def __post_init__(self) -> None:
        self._items = list(dict.fromkeys(self.items))
        self._rng = random.Random(int(self.seed))
        self._queue: Deque[str] = deque()
        self.epoch = 0
        self._start_new_epoch(initial=True)

    def __len__(self) -> int:
        return len(self._items)

    def _start_new_epoch(self, *, initial: bool) -> None:
        self.epoch += 1
        ordered = list(self._items)
        if self.reshuffle_each_epoch or initial:
            self._rng.shuffle(ordered)
        self._queue = deque(ordered)

    def take(self, count: int, banned: Set[str]) -> List[str]:
        if count <= 0 or not self._items:
            return []

        picked: List[str] = []
        picked_set: Set[str] = set()
        epoch_budget = max(1, len(self._items) + 1)

        while len(picked) < count and epoch_budget > 0:
            if not self._queue:
                self._start_new_epoch(initial=False)
                epoch_budget -= 1
                continue

            item = self._queue.popleft()
            if item in banned or item in picked_set:
                continue
            picked.append(item)
            picked_set.add(item)

        return picked


def filter_churn_packages(
    packages: Sequence[str],
    *,
    victim_packages: Sequence[str],
    exclude_victim: bool,
) -> List[str]:
    if not victim_packages or not exclude_victim:
        return list(packages)
    victim_set = set(victim_packages)
    return [pkg for pkg in packages if pkg not in victim_set]


def launch_and_background(
    *,
    serial: str,
    package: str,
    component: str,
    hold_ms: int,
    interactive: bool = False,
    trace_label: Optional[str] = None,
) -> Dict:
    host_ts = time.time()
    row: Dict = {
        "package": package,
        "component": component,
        "host_ts": int(host_ts),
        "host_ts_sec": round(host_ts, 6),
        "hold_ms": int(hold_ms),
        "interactive": interactive,
    }
    if trace_label:
        marker = f"memstress:{trace_label}:begin package={package} component={component}"
        trace_cp = write_trace_marker(serial, marker)
        row["trace_marker_begin_rc"] = trace_cp.returncode
    cp = start_activity(serial, component)
    stdout_text = cp.stdout or ""
    ok = cp.returncode == 0 and "Error:" not in stdout_text and "Exception occurred" not in stdout_text
    row["returncode"] = cp.returncode
    row["stdout_tail"] = stdout_text.strip()[-300:]
    row["stderr_tail"] = (cp.stderr or "").strip()[-300:]
    row["ok"] = ok
    if ok:
        if interactive:
            time.sleep(0.6)
            clicked = interactive_click_loop(serial)
            row["interactive_clicked"] = clicked
        time.sleep(max(0, int(hold_ms) / 1000.0))
        home_cp = exit_to_home(serial)
        row["home_returncode"] = home_cp.returncode
        row["home_stdout_tail"] = (home_cp.stdout or "").strip()[-200:]
        row["home_stderr_tail"] = (home_cp.stderr or "").strip()[-200:]
    if trace_label:
        marker = f"memstress:{trace_label}:end package={package} ok={1 if ok else 0}"
        trace_cp = write_trace_marker(serial, marker)
        row["trace_marker_end_rc"] = trace_cp.returncode
    return row


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

    p.add_argument("--max-cycles", type=int, default=CONFIG["max_cycles"], help="Total memstress cycles to run (replaces duration)")
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
        "--selection-mode",
        choices=["epoch"],
        default=CONFIG["memstress"]["selection_mode"],
        help="Package selection policy. 'epoch' means shuffle once per epoch, then consume without replacement.",
    )
    p.add_argument(
        "--epoch-reshuffle",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["epoch_reshuffle"],
        help="When using --selection-mode epoch, reshuffle package order at each epoch boundary.",
    )
    p.add_argument(
        "--hold-ms",
        type=int,
        default=CONFIG["memstress"]["hold_ms"],
        help="Hold milliseconds *after each successful launch* before pressing HOME to exit foreground (no force-stop).",
    )
    p.add_argument("--launch-gap-ms", type=int, default=CONFIG["memstress"]["launch_gap_ms"])
    p.add_argument("--cycle-sleep-ms", type=int, default=CONFIG["memstress"]["cycle_sleep_ms"])
    p.add_argument(
        "--round-s",
        type=int,
        default=CONFIG["memstress"]["round_s"],
        help="If >0, treat memstress cycles as rounds of ~this duration; at each round boundary force-stop all target packages, then continue immediately.",
    )
    p.add_argument("--seed", type=int, default=CONFIG["memstress"]["seed"], help="Deterministic seed")
    p.add_argument(
        "--mode",
        choices=["launch_only", "interactive"],
        default="launch_only",
        help="launch_only: start+HOME only. interactive: also auto-click consent dialogs via uiautomator.",
    )
    p.add_argument(
        "--clear-logcat",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["clear_logcat"],
        help="Clear logcat before starting workload/log collection",
    )
    p.add_argument(
        "--no-crash-detect",
        action="store_true",
        default=False,
        help="Disable crash signature detection and skip logcat streaming entirely (reduces host IO)",
    )
    p.add_argument("--victim-package", action="append", default=None, help="Victim package(s) to prime and revisit periodically outside the churn set (repeatable)")
    p.add_argument(
        "--victim-exclude-from-churn",
        action=argparse.BooleanOptionalAction,
        default=CONFIG["memstress"]["victim_exclude_from_churn"],
        help="Exclude --victim-package from the churn package pool (default: true)",
    )
    p.add_argument(
        "--victim-prime-hold-ms",
        type=int,
        default=CONFIG["memstress"]["victim_prime_hold_ms"],
        help="Foreground hold time for the one-time victim prime step.",
    )
    p.add_argument(
        "--victim-revisit-every-cycles",
        type=int,
        default=CONFIG["memstress"]["victim_revisit_every_cycles"],
        help="If >0, revisit the victim via the normal launcher path after every N churn cycles.",
    )
    p.add_argument(
        "--victim-revisit-hold-ms",
        type=int,
        default=CONFIG["memstress"]["victim_revisit_hold_ms"],
        help="Foreground hold time for victim revisit launches.",
    )
    p.add_argument("--oat-prune-watch", action="store_true", help="Poll target packages and delete regenerated oat/odex/vdex/art")
    p.add_argument("--oat-prune-package", action="append", default=None, help="Explicit package to watch for oat pruning (repeatable)")
    p.add_argument("--oat-prune-package-file", default=None, help="File with packages to watch for oat pruning")
    p.add_argument("--oat-prune-poll-s", type=float, default=2.0, help="OAT prune poll interval seconds (default: 2.0)")

    p.add_argument("--sample-retries", type=int, default=CONFIG["sample_retries"], help="Sampling retries per tick")
    p.add_argument("--sample-retry-sleep-s", type=int, default=CONFIG["sample_retry_sleep_s"], help="Sleep seconds between sampling retries")

    p.add_argument("--buddyinfo-interval-s", type=int, default=CONFIG["buddyinfo_interval_s"], help="Buddyinfo sampling interval (default 5s, set 0 to disable)")
    p.add_argument("--buddyinfo-thp-counters", type=str, default="", help="Comma-separated THP counter names to append to each buddyinfo row (e.g. 'split,anon_fault_alloc')")
    p.add_argument("--vmstat-interval-s", type=int, default=CONFIG["vmstat_interval_s"], help="Vmstat sampling interval (default 60s, set 0 to disable)")

    p.add_argument("--readahead-min-order", type=int, default=CONFIG["readahead_min_order"], help="Write to /sys/kernel/mm/readahead/min_order before workload starts")
    p.add_argument("--ext4-folio-order", type=int, default=CONFIG["ext4_folio_order"], help="Write to all ext4 min/max_folio_order_cap nodes before workload starts")
    p.add_argument("--f2fs-max-folio-order", type=int, default=CONFIG["f2fs_max_folio_order"], help="Write to all f2fs max_folio_order_cap nodes before workload starts")

    p.add_argument("--no-network-check", action="store_true", default=False, help="Skip network connectivity check before workload")

    return p.parse_args(argv)


def _write_sysfs_if_exists(serial: str, path: str, value: str, *, use_su: bool, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        try:
            out = adb_shell(serial, f"cat {path} 2>/dev/null", use_su=use_su, timeout_s=10, tty=use_su, check=False)
            f.write(f"[write] {path} before={out.strip()}\n")
        except Exception:
            f.write(f"[write] {path} before=<unreadable>\n")
        try:
            adb_shell(serial, f"echo {value} > {path}", use_su=use_su, timeout_s=10, tty=use_su, check=True)
            f.write(f"[write] {path} desired={value}\n")
        except Exception as e:
            f.write(f"[write] {path} ERROR: {e}\n")


def _write_sysfs_glob(serial: str, glob_expr: str, value: str, *, use_su: bool, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = adb_shell(serial, f"for p in {glob_expr}; do [ -e \"$p\" ] && echo \"$p\"; done",
                        use_su=use_su, timeout_s=10, tty=use_su, check=False)
        paths = [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        paths = []
    if not paths:
        return
    for path in paths:
        _write_sysfs_if_exists(serial, path, value, use_su=use_su, log_path=log_path)


def _ensure_network(serial: str) -> None:
    import sys as _sys
    while True:
        cp = adb_shell_cp(serial, "ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1 && echo online || echo offline", timeout_s=10, check=False)
        if cp.stdout and "online" in cp.stdout:
            print(f"[{serial}] network OK")
            return
        print(f"[{serial}] 设备未联网，请连接 WiFi 后继续...", file=_sys.stderr)
        time.sleep(5)


def _auto_detect_stats_dir(serial: str, use_su: bool) -> str:
    sizes = ["16", "32", "64"]
    prefix = "su -c " if use_su else ""
    for size in sizes:
        path = f"/sys/kernel/mm/transparent_hugepage/hugepages-{size}kB/enabled"
        cp = adb_shell_cp(serial, f"{prefix}cat {path}", timeout_s=10, check=False)
        mode = (cp.stdout or "").strip()
        if "[always]" in mode:
            return f"/sys/kernel/mm/transparent_hugepage/hugepages-{size}kB/stats"
    return "/sys/kernel/mm/transparent_hugepage/hugepages-16kB/stats"


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

    max_cycles = max(1, int(args.max_cycles))
    duration_s = max_cycles * max(1, int(args.interval_s))
    round_s = max(0, int(args.round_s))
    counters = [x.strip() for x in str(args.counters).split(",") if x.strip()]
    setup_cmds = CONFIG["setup_shell"] if args.setup_shell is None else list(args.setup_shell)
    interactive = args.mode == "interactive"

    stats_dir = str(args.stats_dir)
    if stats_dir == DEFAULT_STATS_DIR:
        detected = _auto_detect_stats_dir(serial, bool(args.use_su))
        if detected != stats_dir:
            stats_dir = detected
            print(f"[{serial}] Auto-detected stats_dir: {stats_dir}")

    all_pkgs: List[str] = []
    if args.package:
        all_pkgs.extend(args.package)
    else:
        all_pkgs.extend(CONFIG["memstress"]["packages"])
    all_pkgs.extend(read_package_file(args.package_file))
    if args.victim_package:
        all_pkgs.extend(args.victim_package)
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
            "max_cycles": max_cycles,
            "interval_s": int(args.interval_s),
            "stats_dir": stats_dir,
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
                "selection_mode": args.selection_mode,
                "epoch_reshuffle": bool(args.epoch_reshuffle),
                "hold_ms": int(args.hold_ms),
                "launch_gap_ms": int(args.launch_gap_ms),
                "cycle_sleep_ms": int(args.cycle_sleep_ms),
                "round_s": round_s,
                "victim_packages": args.victim_package or [],
                "victim_exclude_from_churn": bool(args.victim_exclude_from_churn),
                "victim_prime_hold_ms": int(args.victim_prime_hold_ms),
                "victim_revisit_every_cycles": int(args.victim_revisit_every_cycles),
                "victim_revisit_hold_ms": int(args.victim_revisit_hold_ms),
                "seed": int(args.seed),
                "clear_logcat": bool(args.clear_logcat),
                "mode": args.mode,
            },
            "oat_prune_watch": {
                "enabled": bool(args.oat_prune_watch),
                "explicit_packages": list(args.oat_prune_package or []),
                "package_file": args.oat_prune_package_file,
                "poll_s": float(args.oat_prune_poll_s),
                "exts": list(DEFAULT_DELETE_EXTS),
            },
            "buddyinfo_interval_s": int(args.buddyinfo_interval_s),
            "buddyinfo_thp_counters": [c.strip() for c in str(args.buddyinfo_thp_counters).split(",") if c.strip()] if args.buddyinfo_thp_counters else [],
            "vmstat_interval_s": int(args.vmstat_interval_s),
            "readahead_min_order": args.readahead_min_order,
            "ext4_folio_order": args.ext4_folio_order,
            "f2fs_max_folio_order": args.f2fs_max_folio_order,
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

        if args.readahead_min_order is not None:
            _write_sysfs_if_exists(serial, "/sys/kernel/mm/readahead/min_order",
                                   str(args.readahead_min_order), use_su=bool(args.use_su),
                                   log_path=out_dir / "sysfs_write_log.txt")

        if args.ext4_folio_order is not None:
            val = str(args.ext4_folio_order)
            for node in ("min_folio_order_cap", "max_folio_order_cap"):
                _write_sysfs_glob(serial, f"/sys/fs/ext4/*/{node}", val,
                                  use_su=bool(args.use_su),
                                  log_path=out_dir / "sysfs_write_log.txt")

        if args.f2fs_max_folio_order is not None:
            _write_sysfs_glob(serial, "/sys/fs/f2fs/*/max_folio_order_cap",
                              str(args.f2fs_max_folio_order), use_su=bool(args.use_su),
                              log_path=out_dir / "sysfs_write_log.txt")

        if not args.no_thp_ensure:
            thp_result = ensure_thp_mode_for_stats(
                serial,
                    stats_dir=stats_dir,
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

        if not args.no_network_check:
            _ensure_network(serial)

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

        raw_victim_packages: List[str] = []
        if args.victim_package:
            raw_victim_packages = unique_preserve_order(args.victim_package)
        victim_packages: List[str] = [pkg.strip() for pkg in raw_victim_packages if pkg.strip()]
        victim_components: Dict[str, str] = {}
        for pkg in victim_packages:
            if pkg not in set(valid_pkgs):
                raise RuntimeError(f"victim package is not installed: {pkg}")
            comp = resolve_activity(serial, pkg)
            if not comp:
                raise RuntimeError(f"victim package is installed but not launchable: {pkg}")
            victim_components[pkg] = comp

        runnable_pkgs = filter_churn_packages(
            runnable_pkgs,
            victim_packages=victim_packages,
            exclude_victim=bool(args.victim_exclude_from_churn),
        )
        if not runnable_pkgs:
            raise RuntimeError("no launchable churn packages remain after victim filtering")

        keywords = [x.strip().lower() for x in str(args.prefer_keywords).split(",") if x.strip()]
        inferred_heavy = classify_heavy_packages(runnable_pkgs, explicit_heavy, keywords)

        manifest["memstress_resolved"] = {
            "packages": runnable_pkgs,
            "explicit_heavy_packages": explicit_heavy,
            "effective_heavy_packages": inferred_heavy,
            "skipped_not_installed": skipped_pkgs,
            "skipped_unresolved": unresolved,
            "resolved_activities": resolved,
            "victim_packages": victim_packages,
            "victim_components": victim_components,
            "victim_excluded_from_churn": bool(victim_packages and args.victim_exclude_from_churn),
        }
        oat_watch_targets = resolve_oat_watch_packages(
            default_packages=valid_pkgs,
            explicit_packages=list(args.oat_prune_package or []),
            file_packages=read_package_file(args.oat_prune_package_file),
        )
        oat_watch_targets = [pkg for pkg in oat_watch_targets if pkg in valid_pkgs]
        manifest["oat_prune_watch_resolved"] = {
            "enabled": bool(args.oat_prune_watch),
            "packages": oat_watch_targets,
            "poll_s": float(args.oat_prune_poll_s),
        }
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        sampling_result = SamplingResult()

        def _sampler() -> None:
            try:
                n, nerr = sample_loop(
                    serial=serial,
                stats_dir=stats_dir,
                    counters=counters,
                    use_su=bool(args.use_su),
                    interval_s=max(1, int(args.interval_s)),
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

        buddyinfo_thread = None
        buddyinfo_thp_counters = [c.strip() for c in str(args.buddyinfo_thp_counters).split(",") if c.strip()] if args.buddyinfo_thp_counters else []
        if int(args.buddyinfo_interval_s) > 0:
            if buddyinfo_thp_counters:
                buddyinfo_thread = threading.Thread(
                    target=buddyinfo_with_thp_sample_loop,
                    kwargs={
                        "serial": serial,
                        "out_csv": out_dir / "buddyinfo_samples.csv",
                        "interval_s": int(args.buddyinfo_interval_s),
                        "counters": buddyinfo_thp_counters,
                        "stats_dir": stats_dir,
                        "use_su": bool(args.use_su),
                        "stop_event": stop,
                    },
                    name=f"buddyinfo_thp_{serial}",
                    daemon=True,
                )
            else:
                buddyinfo_thread = threading.Thread(
                    target=buddyinfo_sample_loop,
                    kwargs={
                        "serial": serial,
                        "out_csv": out_dir / "buddyinfo_samples.csv",
                        "interval_s": int(args.buddyinfo_interval_s),
                        "use_su": bool(args.use_su),
                        "stop_event": stop,
                    },
                    name=f"buddyinfo_{serial}",
                    daemon=True,
                )
            buddyinfo_thread.start()

        vmstat_thread = None
        if int(args.vmstat_interval_s) > 0:
            vmstat_thread = threading.Thread(
                target=vmstat_sample_loop,
                kwargs={
                    "serial": serial,
                    "out_csv": out_dir / "vmstat_samples.csv",
                    "interval_s": int(args.vmstat_interval_s),
                    "use_su": bool(args.use_su),
                    "stop_event": stop,
                },
                name=f"vmstat_{serial}",
                daemon=True,
            )
            vmstat_thread.start()

        crash_signature_event = threading.Event()
        logcat = None
        if not args.no_crash_detect:
            crash_signature_path = memstress_out / "crash_signature.json"
            crash_detector = TargetCrashSignatureDetector(
                serial=serial,
                target_packages=runnable_pkgs,
                window_lines=500,
            )

            def _on_logcat_line(line: str) -> None:
                if crash_signature_event.is_set():
                    return

                payload = crash_detector.process_line(line)
                if payload is not None:
                    try:
                        crash_detector.write_payload(crash_signature_path, payload)
                    except Exception:
                        pass
                    crash_signature_event.set()
                    stop_event.set()

            from utils.adb_utils import start_logcat_stream

            logcat = start_logcat_stream(
                serial,
                memstress_out,
                clear_logcat=bool(args.clear_logcat),
                line_callback=_on_logcat_line,
                stop_event=stop_event,
            )
        oat_watch_thread: Optional[threading.Thread] = None
        if bool(args.oat_prune_watch) and oat_watch_targets:
            oat_watch_thread = threading.Thread(
                target=watch_loop,
                kwargs={
                    "serial": serial,
                    "packages": oat_watch_targets,
                    "out_dir": memstress_out / "oat_watch",
                    "stop_event": stop,
                    "poll_s": float(args.oat_prune_poll_s),
                    "use_su": bool(args.use_su),
                    "exts": DEFAULT_DELETE_EXTS,
                },
                name=f"oat_watch_{serial}",
                daemon=True,
            )
            oat_watch_thread.start()

        cycle_log_f = (memstress_out / "cycle_log.jsonl").open("w", encoding="utf-8")
        try:
            (memstress_out / "resolved_activities.json").write_text(
                json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            start_meminfo = adb_shell_cp(serial, "dumpsys meminfo", timeout_s=60, check=False)
            (memstress_out / "dumpsys_meminfo_start.txt").write_text(start_meminfo.stdout or "", encoding="utf-8")

            all_pool = EpochPackagePool(
                runnable_pkgs,
                seed=int(args.seed),
                reshuffle_each_epoch=bool(args.epoch_reshuffle),
            )
            heavy_pool = EpochPackagePool(
                [pkg for pkg in runnable_pkgs if pkg in set(inferred_heavy)],
                seed=int(args.seed) + 1,
                reshuffle_each_epoch=bool(args.epoch_reshuffle),
            )

            cycle = 0
            rounds = 1
            launched_total = 0
            round_start = time.time()

            manifest["status"] = "running"
            write_run_manifest(out_dir / "run_manifest.json", manifest)

            for vp in victim_packages:
                comp = victim_components.get(vp)
                if not comp:
                    continue
                victim_prime = launch_and_background(
                    serial=serial,
                    package=vp,
                    component=comp,
                    hold_ms=int(args.victim_prime_hold_ms),
                    interactive=interactive,
                    trace_label="victim_prime",
                )
                victim_prime["event"] = "victim_prime"
                cycle_log_f.write(json.dumps(victim_prime, ensure_ascii=False) + "\n")
                cycle_log_f.flush()
                time.sleep(max(0, int(args.launch_gap_ms) / 1000.0))

            while not stop.is_set() and cycle < max_cycles:
                if round_s > 0 and (time.time() - round_start) >= float(round_s):
                    boundary_row = {
                        "event": "round_boundary",
                        "round": rounds,
                        "host_ts": int(time.time()),
                        "force_stop": force_stop_packages(serial, runnable_pkgs),
                    }
                    cycle_log_f.write(json.dumps(boundary_row, ensure_ascii=False) + "\n")
                    cycle_log_f.flush()
                    rounds += 1
                    round_start = time.time()

                cycle += 1
                write_trace_marker(serial, f"memstress:cycle:begin cycle={cycle}")
                chosen: List[str] = []
                chosen_set: Set[str] = set()

                heavy_target = min(int(args.heavy_per_burst), int(args.burst_size), len(heavy_pool))
                for pkg in heavy_pool.take(heavy_target, chosen_set):
                    chosen.append(pkg)
                    chosen_set.add(pkg)

                remain = max(0, int(args.burst_size) - len(chosen))
                for pkg in all_pool.take(remain, chosen_set):
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
                }

                for pkg in chosen:
                    if stop.is_set():
                        break
                    component = resolved[pkg]
                    launch_row = launch_and_background(
                        serial=serial,
                        package=pkg,
                        component=component,
                        hold_ms=int(args.hold_ms),
                        interactive=interactive,
                    )
                    if launch_row["ok"]:
                        cycle_row["launched"].append(pkg)
                        launched_total += 1
                    else:
                        cycle_row["launch_errors"].append({
                            "package": pkg,
                            "returncode": launch_row["returncode"],
                            "stderr": launch_row["stderr_tail"],
                            "stdout_tail": launch_row["stdout_tail"],
                        })
                    time.sleep(max(0, int(args.launch_gap_ms) / 1000.0))

                cycle_log_f.write(json.dumps(cycle_row, ensure_ascii=False) + "\n")
                cycle_log_f.flush()
                write_trace_marker(
                    serial,
                    f"memstress:cycle:end cycle={cycle} launched={len(cycle_row['launched'])} errors={len(cycle_row['launch_errors'])}",
                )
                print(
                    f"[{serial}][memstress] cycle={cycle} launched={len(cycle_row['launched'])} "
                    f"errors={len(cycle_row['launch_errors'])}"
                )

                revisit_every = int(args.victim_revisit_every_cycles)
                if (
                    victim_packages
                    and revisit_every > 0
                    and cycle % revisit_every == 0
                ):
                    victim_idx = (cycle // revisit_every) % len(victim_packages)
                    vp = victim_packages[victim_idx]
                    comp = victim_components.get(vp)
                    if comp:
                        victim_revisit = launch_and_background(
                            serial=serial,
                            package=vp,
                            component=comp,
                            hold_ms=int(args.victim_revisit_hold_ms),
                            interactive=interactive,
                            trace_label="victim_revisit",
                        )
                        victim_revisit["event"] = "victim_revisit"
                        victim_revisit["cycle"] = cycle
                        victim_revisit["victim_idx"] = victim_idx
                        cycle_log_f.write(json.dumps(victim_revisit, ensure_ascii=False) + "\n")
                        cycle_log_f.flush()
                        time.sleep(max(0, int(args.launch_gap_ms) / 1000.0))

                time.sleep(max(0, int(args.cycle_sleep_ms) / 1000.0))

            end_meminfo = adb_shell_cp(serial, "dumpsys meminfo", timeout_s=60, check=False)
            (memstress_out / "dumpsys_meminfo_end.txt").write_text(end_meminfo.stdout or "", encoding="utf-8")

            summary = {
                "serial": serial,
                "cycles": cycle,
                "rounds": rounds,
                "launched_total": launched_total,
                "runnable_packages": len(runnable_pkgs),
                "heavy_packages": inferred_heavy,
                "stopped_by_signal": stop_event.is_set(),
                "crash_signature_found": crash_signature_event.is_set(),
            }
            (memstress_out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False))
        finally:
            try:
                cycle_log_f.close()
            except Exception:
                pass
            if logcat is not None:
                try:
                    logcat.stop()
                except Exception:
                    pass

        # Stop this device's sampler thread (do NOT stop other devices).
        local_stop_event.set()
        if oat_watch_thread is not None:
            oat_watch_thread.join(timeout=max(5.0, float(args.oat_prune_poll_s) * 2.0))
        sampler_thread.join(timeout=30)
        if buddyinfo_thread is not None:
            buddyinfo_thread.join(timeout=15)
        if vmstat_thread is not None:
            vmstat_thread.join(timeout=15)
        if sampling_result.exc:
            raise RuntimeError(f"sampling thread failed: {sampling_result.exc}")

        manifest["samples"] = sampling_result.samples
        manifest["sample_errors"] = sampling_result.errors
        write_run_manifest(out_dir / "run_manifest.json", manifest)

        run_derive_metrics(scripts_dir=scripts_dir, out_dir=out_dir)

        vmstat_raw = out_dir / "vmstat_samples.csv"
        if vmstat_raw.exists():
            derive_vmstat_csv(vmstat_raw, out_dir / "vmstat_derived.csv")

        if crash_signature_event.is_set():
            manifest["status"] = "crash_signature_found"
        else:
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
