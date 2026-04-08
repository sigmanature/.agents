from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .adb_utils import run, adb_shell


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


def read_counters_once(serial: str, stats_dir: str, counters: Sequence[str], *, use_su: bool) -> Sample:
    host_ts = int(time.time())

    parts = [
        "ts=$(date +%s)",
        "echo device_ts=$ts",
    ]
    for c in counters:
        parts.append(f"v=$(cat {stats_dir}/{c} 2>/dev/null || echo '')")
        parts.append(f"echo {c}=$v")

    script = "; ".join(parts)

    try:
        out = adb_shell(serial, script, use_su=use_su, timeout_s=20, tty=False, check=True)
        kv = parse_kv_lines(out)
        dev_ts = int(kv.get("device_ts")) if kv.get("device_ts", "").isdigit() else None
        values: Dict[str, Optional[int]] = {}
        for c in counters:
            s = kv.get(c, "")
            values[c] = int(s) if s.isdigit() else None
        return Sample(host_ts=host_ts, device_ts=dev_ts, values=values, error="")
    except Exception as e:
        return Sample(host_ts=host_ts, device_ts=None, values={str(c): None for c in counters}, error=str(e))


def sample_loop(
    *,
    serial: str,
    stats_dir: str,
    counters: Sequence[str],
    use_su: bool,
    interval_s: int,
    duration_s: int,
    out_csv: Path,
    retries: int,
    retry_sleep_s: int,
    stop_event: Optional[object] = None,
) -> Tuple[int, int]:
    """Returns (num_samples, num_errors).

    stop_event: optional `threading.Event`-like object with `.is_set()`.
    """

    fieldnames = ["host_ts", "device_ts", "error"] + list(counters)
    t0 = time.time()
    t_end = t0 + max(1, duration_s)
    next_t = t0

    num = 0
    num_err = 0

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", None) and stop_event.is_set():
                break

            now = time.time()
            if now >= t_end:
                break

            if now < next_t:
                time.sleep(min(next_t - now, 1.0))
                continue

            s: Optional[Sample] = None
            for _attempt in range(max(1, retries + 1)):
                s = read_counters_once(serial, stats_dir, counters, use_su=use_su)
                if not s.error:
                    break
                time.sleep(max(0, retry_sleep_s))

            assert s is not None
            row = {
                "host_ts": s.host_ts,
                "device_ts": s.device_ts if s.device_ts is not None else "",
                "error": s.error,
            }
            for c in counters:
                v = s.values.get(str(c))
                row[str(c)] = v if v is not None else ""
            w.writerow(row)
            f.flush()

            num += 1
            if s.error:
                num_err += 1

            next_t += max(1, interval_s)

    return num, num_err


def run_derive_metrics(*, scripts_dir: Path, out_dir: Path) -> None:
    derive = scripts_dir / "derive_metrics.py"
    cmd = [sys.executable, str(derive), str(out_dir / "raw_samples.csv"), "--out-dir", str(out_dir)]
    cp = run(cmd, timeout_s=120, check=False)
    (out_dir / "derive_stdout.txt").write_text(cp.stdout or "", encoding="utf-8")
    (out_dir / "derive_stderr.txt").write_text(cp.stderr or "", encoding="utf-8")
    if cp.returncode != 0:
        raise RuntimeError(f"derive_metrics failed rc={cp.returncode}. See derive_stderr.txt")


def write_run_manifest(path: Path, obj: Dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

