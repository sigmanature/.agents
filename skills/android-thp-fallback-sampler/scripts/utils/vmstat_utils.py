"""Periodic /proc/vmstat capture for kswapd + direct reclaim monitoring."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .adb_utils import adb_shell


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
    "compact_daemon_wake",
    "compact_daemon_migrate_scanned",
    "compact_daemon_free_scanned",
    "compact_daemon_order2_created",
    "alloc_success_order0",
    "alloc_success_order2",
    "alloc_fail_wmark",
    "alloc_fail_fragment",
    "alloc_stall_wmark",
    "alloc_stall_fragment",
    "anon_mthp_vma_unsuitable_order2",
    "cow_mthp_order2",
    "cow_mthp_fallback_order0",
    "cow_mthp_vma_unsuitable_order2",
    "kcompactd_timeout_wake",
    "kcompactd_wake_request",
    "kcompactd_woke_by_alloc",
    "kcompactd_woke_by_vmscan",
    "kcompactd_order2_low",
    "kcompactd_wake_from_vmscan",
    "kcompactd_wake_from_alloc",
    "pageoutrun",
    "kswapd_inodesteal",
    "pgfault",
    "pgmajfault",
    "workingset_refault_anon",
    "workingset_refault_file",
    "pswpout",
    "zswpout",
    "swpout_zero",
    "thp_fault_alloc",
    "thp_fault_fallback",
    "thp_swpout",
    "thp_swpout_fallback",
]


def read_vmstat(serial: str, *, use_su: bool = True) -> Dict[str, int]:
    try:
        out = adb_shell(serial, "cat /proc/vmstat", use_su=use_su, timeout_s=15, tty=use_su, check=True)
    except Exception:
        return {}
    result: Dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in VMSTAT_KEYS:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return result


def vmstat_sample_loop(
    *,
    serial: str,
    out_csv: Path,
    interval_s: int,
    use_su: bool,
    stop_event: Optional[object] = None,
) -> Tuple[int, int]:
    fieldnames = ["host_ts"] + VMSTAT_KEYS
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    num = 0
    num_err = 0

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        next_t = time.time()

        while True:
            if stop_event is not None and hasattr(stop_event, "is_set") and stop_event.is_set():
                break

            now = time.time()
            if now < next_t:
                time.sleep(min(next_t - now, 1.0))
                continue

            values = read_vmstat(serial, use_su=use_su)
            row = {"host_ts": int(time.time())}
            err = False
            for k in VMSTAT_KEYS:
                v = values.get(k)
                if v is not None:
                    row[k] = v
                else:
                    row[k] = 0
                    err = True
            w.writerow(row)
            f.flush()
            num += 1
            if err:
                num_err += 1

            next_t += max(1, interval_s)

    return num, num_err


def derive_vmstat_csv(raw_csv: Path, out_csv: Path) -> int:
    rows: List[Dict[str, str]] = []
    with raw_csv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if len(rows) < 2:
        return 0

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

    return len(rows) - 1
