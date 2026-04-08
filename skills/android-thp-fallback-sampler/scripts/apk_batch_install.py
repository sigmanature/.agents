#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch install APK files from a directory to one or more Android devices.

Pure-Python implementation (no bash/grep/awk dependencies).

Inputs:
- apk_dir: directory containing *.apk
- optional serials: repeat --serial to target specific devices; otherwise auto-detect via `adb devices`

Outputs (under output_dir):
- install_log.jsonl: per apk x per device results
- installed_packages.txt: real package names (from pm list packages diff) for APKs installed successfully to ALL target devices
- failed_apks.txt: list of apk filenames that failed on any device

Notes:
- Uses `adb -s SERIAL install -r <apk>`
- Captures stdout/stderr and return code for debugging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
        # Format: SERIAL\tdevice (or offline/unauthorized)
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def list_apks(apk_dir: Path) -> List[Path]:
    if not apk_dir.exists() or not apk_dir.is_dir():
        raise FileNotFoundError(f"apk_dir not found or not a directory: {apk_dir}")

    apks = sorted([p for p in apk_dir.iterdir() if p.is_file() and p.suffix.lower() == ".apk"])
    return apks


_PKG_RE = re.compile(r"([a-zA-Z0-9_]+(?:\\.[a-zA-Z0-9_]+)+)")


def _guess_pkg_from_filename(apk_path: Path) -> Optional[str]:
    m = _PKG_RE.search(apk_path.name)
    if m:
        return m.group(1)
    return None


def _guess_pkg_with_aapt(apk_path: Path) -> Optional[str]:
    # Best-effort: parse manifest via aapt/aapt2 if available.
    for tool in ("aapt2", "aapt"):
        try:
            cp = subprocess.run([tool, "dump", "badging", str(apk_path)], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            continue
        if cp.returncode != 0:
            continue
        # Example: package: name='com.example.app' versionCode='...' versionName='...'
        m = re.search(r"package:\\s+name='([^']+)'", cp.stdout or "")
        if m:
            return m.group(1).strip()
    return None


def infer_package_name(apk_path: Path) -> str:
    # Fallback: infer from filename. Only used if pm diff is unavailable.
    return apk_path.name[: -len(".apk")] if apk_path.name.lower().endswith(".apk") else apk_path.stem


def resolve_apk_package_name(apk_path: Path) -> Optional[str]:
    """Try to resolve real package name for an APK without installing it.

    Used to skip installs for packages already present on the device.
    """

    pkg = _guess_pkg_from_filename(apk_path)
    if pkg:
        return pkg
    return _guess_pkg_with_aapt(apk_path)


def pm_list_packages(serial: str) -> set:
    """Return set of installed package names via adb shell pm list packages."""
    cp = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "list", "packages"],
        capture_output=True, text=True, timeout=60,
    )
    pkgs = set()
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.add(line[len("package:"):].strip())
    return pkgs


def service_check(serial: str, name: str) -> bool:
    """Best-effort check whether an Android binder service exists."""
    cp = subprocess.run(
        ["adb", "-s", serial, "shell", "service", "check", name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = ((cp.stdout or "") + "\n" + (cp.stderr or "")).lower()
    return "found" in out


def wait_for_services(serial: str, *, timeout_s: int = 60, sleep_s: float = 2.0) -> bool:
    """Wait until core services needed by `adb install` are available."""
    deadline = time.time() + max(1, int(timeout_s))
    while time.time() < deadline:
        try:
            if service_check(serial, "package") and service_check(serial, "mount"):
                return True
        except Exception:
            pass
        time.sleep(max(0.2, float(sleep_s)))
    return False


def is_transient_install_error(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    # Observed during large batch installs if system_server / services are restarting.
    markers = [
        "can't find service: package",
        "failure calling service package",
        "broken pipe",
        "device offline",
        # Observed NPE in PackageManagerShellCommand on some builds when StorageManager is not ready.
        "nullpointerexception",
        "installlocationutils",
        "storagemanager.getvolumes",
    ]
    return any(m in t for m in markers)


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
    attempts: int = 1


def install_one(serial: str, apk_path: Path, timeout_s: int, *, retries: int, retry_sleep_s: float) -> InstallRecord:
    t0 = time.time()
    pkg = infer_package_name(apk_path)

    last: Optional[subprocess.CompletedProcess] = None
    attempts = 0

    for attempt in range(1, max(1, int(retries) + 1) + 1):
        attempts = attempt
        try:
            cp = subprocess.run(
                ["adb", "-s", serial, "install", "-r", str(apk_path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            last = cp
            ok = (cp.returncode == 0) and ("Success" in (cp.stdout + cp.stderr))
            if ok:
                return InstallRecord(
                    serial=serial,
                    apk=apk_path.name,
                    package_name=pkg,
                    ok=True,
                    returncode=cp.returncode,
                    stdout=cp.stdout.strip(),
                    stderr=cp.stderr.strip(),
                    elapsed_ms=int((time.time() - t0) * 1000),
                    attempts=attempts,
                )

            combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
            if attempt <= int(retries) and is_transient_install_error(combined):
                wait_for_services(serial, timeout_s=60, sleep_s=2.0)
                time.sleep(max(0.5, float(retry_sleep_s)))
                continue
            break
        except subprocess.TimeoutExpired as e:
            out = (e.stdout.decode("utf-8", "ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")).strip()
            err = (e.stderr.decode("utf-8", "ignore") if isinstance(e.stderr, bytes) else (e.stderr or "")).strip()
            combined = out + "\n" + err
            if attempt <= int(retries) and is_transient_install_error(combined):
                wait_for_services(serial, timeout_s=60, sleep_s=2.0)
                time.sleep(max(0.5, float(retry_sleep_s)))
                continue
            return InstallRecord(
                serial=serial,
                apk=apk_path.name,
                package_name=pkg,
                ok=False,
                returncode=124,
                stdout=out,
                stderr=err or f"timeout after {timeout_s}s",
                elapsed_ms=int((time.time() - t0) * 1000),
                attempts=attempts,
            )

    if last is None:
        return InstallRecord(
            serial=serial,
            apk=apk_path.name,
            package_name=pkg,
            ok=False,
            returncode=1,
            stdout="",
            stderr="install failed (no subprocess result)",
            elapsed_ms=int((time.time() - t0) * 1000),
            attempts=attempts or 1,
        )
    return InstallRecord(
        serial=serial,
        apk=apk_path.name,
        package_name=pkg,
        ok=False,
        returncode=last.returncode,
        stdout=(last.stdout or "").strip(),
        stderr=(last.stderr or "").strip(),
        elapsed_ms=int((time.time() - t0) * 1000),
        attempts=attempts or 1,
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
    p.add_argument(
        "--skip-installed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip installing APKs whose package name already exists on the device (best-effort).",
    )
    p.add_argument("--retries", type=int, default=2, help="Retry count for transient install failures (default: 2)")
    p.add_argument("--retry-sleep", type=float, default=3.0, help="Seconds between install retries (default: 3.0)")
    p.add_argument("--gap-s", type=float, default=0.0, help="Sleep seconds between APK installs (default: 0.0)")

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

    # Snapshot packages before install (per serial)
    pkgs_before: Dict[str, set] = {}
    for serial in serials:
        print(f"[{serial}] Snapshotting installed packages (before)...")
        pkgs_before[serial] = pm_list_packages(serial)
        # Some devices/services may still be coming up; wait a bit for stability.
        wait_for_services(serial, timeout_s=120, sleep_s=2.0)

    # Track per-apk per-device success
    by_apk: Dict[str, Dict[str, bool]] = {}

    for apk in apks:
        resolved_pkg = resolve_apk_package_name(apk) if args.skip_installed else None
        by_apk.setdefault(apk.name, {})
        print(f"Installing: {apk.name}")
        for serial in serials:
            if args.skip_installed and resolved_pkg and (resolved_pkg in pkgs_before.get(serial, set())):
                r = InstallRecord(
                    serial=serial,
                    apk=apk.name,
                    package_name=resolved_pkg,
                    ok=True,
                    returncode=0,
                    stdout="SKIP (already installed)",
                    stderr="",
                    elapsed_ms=0,
                )
                records.append(r)
                by_apk[apk.name][serial] = True
                print(f"  [{serial}] SKIP package={resolved_pkg} (already installed)")
                continue

            r = install_one(
                serial,
                apk,
                timeout_s=int(args.timeout),
                retries=int(args.retries),
                retry_sleep_s=float(args.retry_sleep),
            )
            records.append(r)
            by_apk[apk.name][serial] = r.ok
            status = "OK" if r.ok else "FAIL"
            print(f"  [{serial}] {status} rc={r.returncode} {r.stderr or r.stdout}")
            if float(args.gap_s) > 0:
                time.sleep(max(0.0, float(args.gap_s)))

    write_jsonl(log_path, [asdict(r) for r in records])

    # Snapshot packages after install (per serial) and compute diff
    pkgs_after: Dict[str, set] = {}
    for serial in serials:
        print(f"[{serial}] Snapshotting installed packages (after)...")
        pkgs_after[serial] = pm_list_packages(serial)

    # New packages = union of per-device diffs (installed on at least one device)
    new_pkgs_union: set = set()
    for serial in serials:
        new_pkgs_union |= (pkgs_after[serial] - pkgs_before[serial])

    installed_pkgs: List[str] = []
    failed_apks: List[str] = []

    for apk in apks:
        ok_all = all(by_apk.get(apk.name, {}).get(s, False) for s in serials)
        if ok_all:
            # Find the real package name from the pm diff; fall back to filename inference
            inferred = (resolve_apk_package_name(apk) or infer_package_name(apk))
            # Try to match against new packages: prefer exact match on inferred name,
            # then any new pkg that contains the stem (best-effort for renamed apks)
            if inferred in new_pkgs_union:
                installed_pkgs.append(inferred)
            else:
                stem = apk.stem.lower()
                candidates = [p for p in new_pkgs_union if stem in p.lower() or p.lower() in stem]
                if candidates:
                    installed_pkgs.append(candidates[0])
                else:
                    # Last resort: use filename inference
                    installed_pkgs.append(inferred)
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
