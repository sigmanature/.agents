#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


AM_CRASH_RE = re.compile(r"\bam_crash:\s*\[(.*)\]")
PACKAGE_RE = re.compile(r"^(?:Package )?\[(?P<package>[^\]]+)\]")
STATUS_RE = re.compile(
    r"^\s*(?P<abi>[^:\s]+):\s+"
    r"\[status=(?P<status>[^\]]+)\]\s+"
    r"\[reason=(?P<reason>[^\]]+)\]"
)
LOCATION_RE = re.compile(r"^\s*\[location is (?P<location>.+)\]\s*$")


def run(cmd: list[str], *, text: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=text, timeout=timeout)


def adb_base(serial: str) -> list[str]:
    return ["adb", "-s", serial]


def adb_shell_text(serial: str, cmd: str, *, use_su: bool, timeout: int = 60) -> subprocess.CompletedProcess:
    if use_su:
        return run(adb_base(serial) + ["shell", "su", "-c", cmd], timeout=timeout)
    return run(adb_base(serial) + ["shell", cmd], timeout=timeout)


def adb_exec_out_bytes(serial: str, cmd: str, *, use_su: bool, timeout: int = 60) -> subprocess.CompletedProcess:
    if use_su:
        return run(adb_base(serial) + ["exec-out", "su", "-c", cmd], text=False, timeout=timeout)
    return run(adb_base(serial) + ["exec-out", "sh", "-c", cmd], text=False, timeout=timeout)


def clear_logcat(serial: str) -> None:
    run(adb_base(serial) + ["logcat", "-c"], timeout=20)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch target package am_crash and inspect live .vdex tail.")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--package", action="append", required=True, help="Target package (repeatable)")
    parser.add_argument("--abi", default="arm64")
    parser.add_argument("--page-size", type=int, default=4096)
    parser.add_argument("--clear-logcat", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--use-su", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout-s", type=int, default=0, help="0 means no timeout")
    parser.add_argument("--inspect-package-now", help="Skip logcat watch and inspect this package immediately")
    return parser.parse_args()


def parse_am_crash_package(line: str) -> str:
    match = AM_CRASH_RE.search(line)
    if not match:
        return ""
    payload = [item.strip() for item in match.group(1).split(",")]
    if len(payload) < 3:
        return ""
    return payload[2]


def parse_pm_art_dump_location(text: str, abi: str) -> str:
    current_pkg = ""
    current_abi = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        pkg_match = PACKAGE_RE.match(line)
        if pkg_match:
            current_pkg = pkg_match.group("package")
            current_abi = ""
            continue
        status_match = STATUS_RE.match(line)
        if status_match:
            current_abi = status_match.group("abi")
            continue
        loc_match = LOCATION_RE.match(line)
        if loc_match and current_pkg and current_abi == abi:
            return loc_match.group("location")
    return ""


def shell_quote_single(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def resolve_live_vdex_path(serial: str, pkg: str, abi: str, use_su: bool) -> tuple[str, dict]:
    pm_cp = adb_shell_text(serial, f"pm art dump {pkg}", use_su=False, timeout=60)
    pm_text = pm_cp.stdout or ""
    location = parse_pm_art_dump_location(pm_text, abi)
    metadata = {
        "pm_art_dump_rc": pm_cp.returncode,
        "pm_art_dump_location": location,
    }

    candidates: list[str] = []
    if location:
        loc_path = Path(location)
        candidates.append(str(loc_path.with_suffix(".vdex")))
        candidates.append(str(loc_path.parent / "base.vdex"))

    find_cmd = (
        "for p in "
        f"/data/app/*/{pkg}-*/oat/{abi}/base.vdex "
        f"/data/app/*/{pkg}*/oat/{abi}/base.vdex "
        "; do [ -f \"$p\" ] && echo \"$p\"; done"
    )
    find_cp = adb_shell_text(serial, find_cmd, use_su=use_su, timeout=60)
    for line in (find_cp.stdout or "").splitlines():
        line = line.strip()
        if line:
            candidates.append(line)
    metadata["find_vdex_rc"] = find_cp.returncode
    metadata["find_vdex_stdout"] = [line.strip() for line in (find_cp.stdout or "").splitlines() if line.strip()]

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        test_cp = adb_shell_text(serial, f"test -f {shell_quote_single(candidate)} && echo OK", use_su=use_su, timeout=30)
        if "OK" in (test_cp.stdout or ""):
            return candidate, metadata
    return "", metadata


def inspect_tail(serial: str, path: str, *, page_size: int, use_su: bool) -> dict:
    stat_cp = adb_shell_text(serial, f"stat -c %s {shell_quote_single(path)}", use_su=use_su, timeout=30)
    if stat_cp.returncode != 0:
        raise RuntimeError((stat_cp.stderr or stat_cp.stdout or "stat failed").strip())

    size = int((stat_cp.stdout or "").strip().splitlines()[-1])
    tail_len = size % page_size
    result = {
        "path": path,
        "size": size,
        "page_size": page_size,
        "tail_len": tail_len,
        "tail_offset": size - tail_len if tail_len else size,
        "tail_exists": bool(tail_len),
        "tail_all_zero": None,
        "tail_hex_last64": "",
    }
    if tail_len == 0:
        return result

    read_cmd = f"tail -c {tail_len} {shell_quote_single(path)}"
    tail_cp = adb_exec_out_bytes(serial, read_cmd, use_su=use_su, timeout=max(60, min(600, tail_len // 4096 + 60)))
    if tail_cp.returncode != 0:
        stderr = tail_cp.stderr.decode("utf-8", "ignore") if isinstance(tail_cp.stderr, bytes) else (tail_cp.stderr or "")
        raise RuntimeError((stderr or "tail read failed").strip())
    tail_bytes = tail_cp.stdout or b""
    if len(tail_bytes) != tail_len:
        raise RuntimeError(f"tail length mismatch: expected {tail_len}, got {len(tail_bytes)}")

    result["tail_all_zero"] = all(b == 0 for b in tail_bytes)
    result["tail_hex_last64"] = tail_bytes[-64:].hex()
    return result


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inspect_package_now(args: argparse.Namespace, pkg: str) -> int:
    vdex_path, metadata = resolve_live_vdex_path(args.serial, pkg, args.abi, bool(args.use_su))
    result = {
        "host_ts": int(time.time()),
        "matched_package": pkg,
        **metadata,
        "vdex_path": vdex_path,
    }
    if not vdex_path:
        result["error"] = "failed to resolve live .vdex path"
        write_json(args.out_dir / "vdex_tail_result.json", result)
        return 2
    try:
        result.update(inspect_tail(args.serial, vdex_path, page_size=int(args.page_size), use_su=bool(args.use_su)))
    except Exception as exc:
        result["error"] = str(exc)
        write_json(args.out_dir / "vdex_tail_result.json", result)
        return 3
    write_json(args.out_dir / "vdex_tail_result.json", result)
    return 0


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clear_logcat:
        clear_logcat(args.serial)

    manifest = {
        "serial": args.serial,
        "packages": list(dict.fromkeys(args.package)),
        "abi": args.abi,
        "page_size": args.page_size,
        "use_su": bool(args.use_su),
        "timeout_s": int(args.timeout_s),
        "start_host_ts": int(time.time()),
    }
    write_json(out_dir / "watch_manifest.json", manifest)

    if args.inspect_package_now:
        return inspect_package_now(args, args.inspect_package_now)

    log_path = out_dir / "watcher_logcat_threadtime.txt"
    event_path = out_dir / "am_crash_event.json"
    result_path = out_dir / "vdex_tail_result.json"

    proc = subprocess.Popen(
        adb_base(args.serial) + ["logcat", "-v", "threadtime", "-b", "all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    deadline = time.time() + int(args.timeout_s) if int(args.timeout_s) > 0 else None

    with log_path.open("w", encoding="utf-8") as log_fh:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                pkg = parse_am_crash_package(line)
                if pkg not in manifest["packages"]:
                    if deadline is not None and time.time() > deadline:
                        break
                    continue

                event = {
                    "host_ts": int(time.time()),
                    "matched_package": pkg,
                    "matched_line": line.rstrip("\n"),
                }
                write_json(event_path, event)

                vdex_path, metadata = resolve_live_vdex_path(args.serial, pkg, args.abi, bool(args.use_su))
                result = {
                    **event,
                    **metadata,
                    "vdex_path": vdex_path,
                }
                if not vdex_path:
                    result["error"] = "failed to resolve live .vdex path"
                    write_json(result_path, result)
                    return 2

                try:
                    result.update(inspect_tail(args.serial, vdex_path, page_size=int(args.page_size), use_su=bool(args.use_su)))
                except Exception as exc:
                    result["error"] = str(exc)
                    write_json(result_path, result)
                    return 3

                write_json(result_path, result)
                return 0

                if deadline is not None and time.time() > deadline:
                    break
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    timeout_payload = {
        "serial": args.serial,
        "packages": manifest["packages"],
        "timeout_s": int(args.timeout_s),
        "timed_out": True,
        "end_host_ts": int(time.time()),
    }
    write_json(out_dir / "watch_timeout.json", timeout_payload)
    return 124


if __name__ == "__main__":
    sys.exit(main())
