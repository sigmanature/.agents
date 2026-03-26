#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch install APK files from a directory to one or more Android devices.

Pure-Python implementation (no bash/grep/awk dependencies).

Inputs:
- apk_dir: directory containing *.apk
- optional serials: repeat --serial to target specific devices; otherwise auto-detect via `adb devices`

Outputs (under output_dir):
- install_log.jsonl: per apk x per device results
- installed_packages.txt: package names inferred from apk filename (basename without .apk) for APKs installed successfully to ALL target devices
- failed_apks.txt: list of apk filenames that failed on any device

Notes:
- Uses `adb -s SERIAL install -r <apk>`
- Captures stdout/stderr and return code for debugging.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def adb_devices() -> List[str]:
    try:
        cp = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise RuntimeError("adb not found in PATH")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"adb devices failed: {e.stderr.strip()}")

    serials: List[str] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def list_apks(apk_dir: Path) -> List[Path]:
    if not apk_dir.exists() or not apk_dir.is_dir():
        raise FileNotFoundError(f"apk_dir not found or not a directory: {apk_dir}")

    return sorted([p for p in apk_dir.iterdir() if p.is_file() and p.suffix.lower() == ".apk"])


def infer_package_name(apk_path: Path) -> str:
    return apk_path.name[: -len(".apk")] if apk_path.name.lower().endswith(".apk") else apk_path.stem


@dataclass
class InstallRecord:
    serial: str
    apk: str
    package_name: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: int


def install_one(serial: str, apk_path: Path, timeout_s: int) -> InstallRecord:
    t0 = time.time()
    pkg = infer_package_name(apk_path)

    try:
        cp = subprocess.run(
            ["adb", "-s", serial, "install", "-r", str(apk_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        ok = (cp.returncode == 0) and ("Success" in (cp.stdout + cp.stderr))
        return InstallRecord(
            serial=serial,
            apk=apk_path.name,
            package_name=pkg,
            ok=ok,
            returncode=cp.returncode,
            stdout=cp.stdout.strip(),
            stderr=cp.stderr.strip(),
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    except subprocess.TimeoutExpired as e:
        return InstallRecord(
            serial=serial,
            apk=apk_path.name,
            package_name=pkg,
            ok=False,
            returncode=124,
            stdout=(e.stdout.decode("utf-8", "ignore").strip() if isinstance(e.stdout, bytes) else (e.stdout or "").strip()),
            stderr=(e.stderr.decode("utf-8", "ignore").strip() if isinstance(e.stderr, bytes) else (e.stderr or "").strip())
            or f"timeout after {timeout_s}s",
            elapsed_ms=int((time.time() - t0) * 1000),
        )


def write_jsonl(path: Path, rows: List[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Batch install APKs to Android devices")
    p.add_argument("apk_dir", help="Directory containing *.apk")
    p.add_argument(
        "--serial",
        action="append",
        default=[],
        help="Target device serial (repeatable). If omitted, auto-detect from `adb devices`.",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Default: output/apk_install_<timestamp>/",
    )
    p.add_argument("--timeout", type=int, default=180, help="adb install timeout seconds")

    args = p.parse_args(argv)

    apk_dir = Path(args.apk_dir)
    apks = list_apks(apk_dir)
    if not apks:
        print(f"No .apk files found under {apk_dir}", file=sys.stderr)
        return 2

    serials = args.serial or adb_devices()
    if not serials:
        print("No connected devices (adb devices shows none in 'device' state)", file=sys.stderr)
        return 3

    output_dir = Path(args.output_dir) if args.output_dir else Path("output") / f"apk_install_{_now_ts()}"
    _mkdir(output_dir)

    log_path = output_dir / "install_log.jsonl"
    installed_path = output_dir / "installed_packages.txt"
    failed_path = output_dir / "failed_apks.txt"

    records: List[InstallRecord] = []
    by_apk: Dict[str, Dict[str, bool]] = {}

    for apk in apks:
        by_apk.setdefault(apk.name, {})
        print(f"Installing: {apk.name}")
        for serial in serials:
            r = install_one(serial, apk, timeout_s=args.timeout)
            records.append(r)
            by_apk[apk.name][serial] = r.ok
            status = "OK" if r.ok else "FAIL"
            print(f"  [{serial}] {status} rc={r.returncode} {r.stderr or r.stdout}")

    write_jsonl(log_path, [asdict(r) for r in records])

    installed_pkgs: List[str] = []
    failed_apks: List[str] = []

    for apk in apks:
        pkg = infer_package_name(apk)
        ok_all = all(by_apk.get(apk.name, {}).get(s, False) for s in serials)
        if ok_all:
            installed_pkgs.append(pkg)
        else:
            failed_apks.append(apk.name)

    installed_path.write_text("\n".join(installed_pkgs) + ("\n" if installed_pkgs else ""), encoding="utf-8")
    failed_path.write_text("\n".join(failed_apks) + ("\n" if failed_apks else ""), encoding="utf-8")

    print(f"Output dir: {output_dir}")
    print(f"Devices: {', '.join(serials)}")
    print(f"APKs: {len(apks)} | ok(all devices): {len(installed_pkgs)} | failed(any device): {len(failed_apks)}")
    print(f"Log: {log_path}")

    return 0 if not failed_apks else 1


if __name__ == "__main__":
    raise SystemExit(main())
