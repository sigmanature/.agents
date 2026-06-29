#!/usr/bin/env python3
"""Collect and optionally set Android THP and filesystem folio-cap state."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from utils.adb_utils import adb_shell_cp, ensure_adb_works, resolve_serial


TRACE_EVENTS = [
    "kmem:mm_page_alloc",
    "vmscan:mm_vmscan_direct_reclaim_begin",
    "vmscan:mm_vmscan_direct_reclaim_end",
    "compaction:mm_compaction_try_to_compact_pages",
    "compaction:mm_compaction_begin",
    "compaction:mm_compaction_end",
    "readahead:page_cache_ra_order",
]


def shell(serial: str, cmd: str, *, use_su: bool = True, check: bool = False, tty: bool = False) -> str:
    del tty
    remote_cmd = f"su -c {shlex.quote(cmd)}" if use_su else cmd
    cp = adb_shell_cp(serial, remote_cmd, timeout_s=30, check=False)
    if check and cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "adb shell failed").strip())
    return cp.stdout or ""


def read_path(serial: str, path: str, *, use_su: bool = True) -> Dict[str, object]:
    quoted = shlex.quote(path)
    exists_out = shell(serial, f"[ -e {quoted} ] && echo yes || echo no", use_su=use_su, check=False)
    exists = exists_out.strip().splitlines()[-1:] == ["yes"]
    out = shell(serial, f"cat {quoted} 2>/dev/null || true", use_su=use_su, check=False) if exists else ""
    return {"path": path, "exists": exists, "value": out.strip()}


def write_path(serial: str, path: str, value: str, *, use_su: bool = True) -> Dict[str, object]:
    result = {"path": path, "desired": value, "returncode": 0, "error": ""}
    try:
        shell(serial, f"echo {shlex.quote(value)} > {shlex.quote(path)}", use_su=use_su, check=True, tty=use_su)
    except Exception as e:
        result["returncode"] = 1
        result["error"] = str(e)
    result["after"] = read_path(serial, path, use_su=use_su).get("value", "")
    return result


def write_folio_caps_bulk(serial: str, value: str, *, use_su: bool = True) -> None:
    value_q = shlex.quote(value)
    cmd = (
        "for d in /sys/fs/ext4 /sys/fs/f2fs; do "
        f"for f in $d/*/max_folio_order_cap $d/*/min_folio_order_cap; do "
        f"[ -w \"$f\" ] && echo {value_q} > \"$f\" 2>/dev/null; "
        "done; done"
    )
    shell(serial, cmd, use_su=use_su, check=False)


def list_dirs(serial: str, glob_expr: str, *, use_su: bool = True) -> List[str]:
    out = shell(serial, f"for p in {glob_expr}; do [ -e \"$p\" ] && echo \"$p\"; done", use_su=use_su, check=False)
    return [line.strip() for line in out.splitlines() if line.strip()]


def collect(serial: str, args: argparse.Namespace) -> Dict[str, object]:
    report: Dict[str, object] = {
        "serial": serial,
        "host_ts": int(time.time()),
        "actions": [],
        "transparent_hugepage": {},
        "filesystem_folio_caps": {},
        "tracefs": {},
    }

    thp_paths = [
        "/sys/kernel/mm/transparent_hugepage/enabled",
        "/sys/kernel/mm/transparent_hugepage/defrag",
    ]
    for hp in list_dirs(serial, "/sys/kernel/mm/transparent_hugepage/hugepages-*kB", use_su=args.use_su):
        thp_paths.extend([f"{hp}/enabled", f"{hp}/anon"])
        for stat in ("anon_fault_alloc", "anon_fault_fallback", "anon_fault_fallback_charge"):
            thp_paths.append(f"{hp}/stats/{stat}")

    if args.thp_enabled:
        report["actions"].append(write_path(serial, "/sys/kernel/mm/transparent_hugepage/enabled", args.thp_enabled, use_su=args.use_su))
    if args.thp_defrag:
        report["actions"].append(write_path(serial, "/sys/kernel/mm/transparent_hugepage/defrag", args.thp_defrag, use_su=args.use_su))
    mthp_mode = args.mthp_enabled or args.mthp_anon
    if mthp_mode:
        for hp in list_dirs(serial, "/sys/kernel/mm/transparent_hugepage/hugepages-*kB", use_su=args.use_su):
            targets = []
            for leaf in ("enabled", "anon"):
                candidate = f"{hp}/{leaf}"
                if read_path(serial, candidate, use_su=args.use_su).get("exists"):
                    targets.append(candidate)
            for target in targets:
                report["actions"].append(write_path(serial, target, mthp_mode, use_su=args.use_su))

    report["transparent_hugepage"] = {p: read_path(serial, p, use_su=args.use_su) for p in sorted(set(thp_paths))}

    fs_paths: List[str] = []
    fs_paths.extend(list_dirs(serial, "/sys/fs/f2fs/*/max_folio_order_cap", use_su=args.use_su))
    fs_paths.extend(list_dirs(serial, "/sys/fs/f2fs/*/min_folio_order_cap", use_su=args.use_su))
    fs_paths.extend(list_dirs(serial, "/sys/fs/ext4/*/max_folio_order_cap", use_su=args.use_su))
    fs_paths.extend(list_dirs(serial, "/sys/fs/ext4/*/min_folio_order_cap", use_su=args.use_su))

    if args.folio_cap is not None:
        write_folio_caps_bulk(serial, str(args.folio_cap), use_su=args.use_su)
        report["actions"].append({"bulk_folio_cap": str(args.folio_cap), "returncode": 0})

    report["filesystem_folio_caps"] = {p: read_path(serial, p, use_su=args.use_su) for p in sorted(set(fs_paths))}

    available = shell(serial, "cat /sys/kernel/tracing/available_events 2>/dev/null", use_su=args.use_su, check=False)
    report["tracefs"] = {
        "available_events_exists": bool(available.strip()),
        "required_events": {name: name in available for name in TRACE_EVENTS},
        "trace_marker": read_path(serial, "/sys/kernel/tracing/trace_marker", use_su=args.use_su),
    }
    return report


def write_report(report: Dict[str, object], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# THP Folio Cap Preflight\n"]
    lines.append(f"- serial: `{report['serial']}`\n")
    lines.append(f"- host_ts: `{report['host_ts']}`\n")
    lines.append("\n## Trace Events\n")
    tracefs = report["tracefs"]
    for name, ok in tracefs["required_events"].items():
        lines.append(f"- `{name}`: {'yes' if ok else 'missing'}\n")
    lines.append("\n## Filesystem Folio Caps\n")
    caps = report["filesystem_folio_caps"]
    if caps:
        for path, item in caps.items():
            lines.append(f"- `{path}`: `{item.get('value', '')}`\n")
    else:
        lines.append("- no folio cap sysfs nodes found\n")
    lines.append("\n## Actions\n")
    actions = report["actions"]
    if actions:
        for action in actions:
            lines.append(f"- `{action.get('path')}` desired=`{action.get('desired')}` after=`{action.get('after')}` rc={action.get('returncode')}\n")
    else:
        lines.append("- none\n")
    (out_dir / "preflight.md").write_text("".join(lines), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Preflight THP and filesystem folio cap state")
    p.add_argument("--serial", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--use-su", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--thp-enabled", choices=["always", "madvise", "never"], default=None)
    p.add_argument("--thp-defrag", choices=["always", "defer", "defer+madvise", "madvise", "never"], default=None)
    p.add_argument("--mthp-enabled", choices=["always", "inherit", "madvise", "never"], default=None)
    p.add_argument("--mthp-anon", choices=["always", "inherit", "madvise", "never"], default=None, help="Deprecated alias; use --mthp-enabled")
    p.add_argument("--folio-cap", type=int, default=None, help="Set every discovered f2fs/ext4 min/max folio cap node to this value")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()
    serial = resolve_serial(args.serial)
    out_dir = Path(args.out_dir or f"preflight_thp_folio_{time.strftime('%Y%m%d_%H%M%S')}")
    report = collect(serial, args)
    write_report(report, out_dir)
    print(f"Results saved to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
