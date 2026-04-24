#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import threading
from pathlib import Path
from typing import List, Optional

from utils.adb_utils import ensure_adb_works, resolve_serials
from utils.oat_watch import DEFAULT_DELETE_EXTS, watch_loop
from utils.pkg_utils import read_package_file, unique_preserve_order
from utils.task_pool import TaskPool


def install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Poll target packages and delete regenerated oat/odex/vdex/art files")
    p.add_argument("--serial", action="append", default=[], help="Target device serial (repeatable or comma-separated)")
    p.add_argument("--all-devices", action="store_true", help="Run on all adb devices in 'device' state")
    p.add_argument("--jobs", type=int, default=0, help="Max parallel devices; default=len(serials)")
    p.add_argument("--out-dir", required=True, help="Output directory")
    p.add_argument("--package", action="append", default=None, help="Target package (repeatable)")
    p.add_argument("--package-file", default=None, help="Package list file")
    p.add_argument("--poll-s", type=float, default=2.0, help="Poll interval seconds (default: 2.0)")
    p.add_argument("--until-host-pid", type=int, default=0, help="Exit when this host PID no longer exists")
    p.add_argument(
        "--use-su",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use su -c for deletion commands (default: true)",
    )
    p.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Artifact extension to delete (repeatable, default: odex/vdex/art/oat)",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    ensure_adb_works()

    pkgs = list(args.package or []) + read_package_file(args.package_file)
    pkgs = unique_preserve_order([x for x in pkgs if x])
    if not pkgs:
        raise RuntimeError("watch_oat_prune requires --package and/or --package-file")

    serials = resolve_serials(args.serial, all_devices=bool(args.all_devices))
    jobs = int(args.jobs) if int(args.jobs) > 0 else max(1, len(serials))
    top_out_dir = Path(args.out_dir).resolve()
    top_out_dir.mkdir(parents=True, exist_ok=True)
    exts = tuple(args.ext or DEFAULT_DELETE_EXTS)

    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    if len(serials) > 1:
        print(json.dumps({"devices": serials, "jobs": jobs, "out_dir": str(top_out_dir)}, ensure_ascii=False))

    pool = TaskPool(max_workers=jobs)
    futures = {}
    try:
        for serial in serials:
            out_dir = top_out_dir / serial if len(serials) > 1 else top_out_dir
            futures[serial] = pool.submit(
                serial,
                watch_loop,
                serial=serial,
                packages=pkgs,
                out_dir=out_dir,
                stop_event=stop_event,
                poll_s=float(args.poll_s),
                use_su=bool(args.use_su),
                exts=exts,
                until_host_pid=int(args.until_host_pid),
            )
        results = pool.gather(futures, stop_event=stop_event, fail_fast=False)
        for row in results:
            if not row.ok:
                print(json.dumps({"serial": row.name, "ok": False, "error": row.error}, ensure_ascii=False))
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
