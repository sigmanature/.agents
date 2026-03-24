#!/usr/bin/env python3
"""Unpack a Pixel 6 vendor_boot.img (header v4) using AOSP unpack_bootimg.py.

This script exists mainly so the agent has a deterministic command to run and
so later scripts can rely on a known output directory layout.

Expected output (AOSP unpack_bootimg.py):
  <out>/vendor-ramdisk-by-name/ramdisk_ -> ../vendor_ramdisk00
  <out>/vendor_ramdisk00 (lz4 legacy)
  <out>/vendor_ramdisk01 (often dlkm)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def which(p: str) -> str | None:
    return shutil.which(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor-boot", required=True, help="Path to vendor_boot.img")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument(
        "--unpack-tool",
        default=None,
        help="Path to AOSP unpack_bootimg.py. If omitted, tries to find it in PATH.",
    )
    args = ap.parse_args()

    vb = Path(args.vendor_boot).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()

    if not vb.exists():
        print(f"ERROR: vendor_boot not found: {vb}", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)

    unpack_tool = args.unpack_tool
    if unpack_tool is None:
        # Common name in AOSP host tools; some users add it to PATH.
        unpack_tool = which("unpack_bootimg.py")

    if unpack_tool is None:
        print(
            "ERROR: unpack_bootimg.py not found. Provide --unpack-tool pointing to your AOSP host tool.\n"
            "Hint: it is usually under out/host/linux-x86/bin/unpack_bootimg.py in your AOSP build tree.",
            file=sys.stderr,
        )
        return 2

    unpack_tool_path = Path(unpack_tool).expanduser().resolve()
    if not unpack_tool_path.exists():
        print(f"ERROR: unpack tool not found: {unpack_tool_path}", file=sys.stderr)
        return 2

    # Run the tool.
    try:
        run([sys.executable, str(unpack_tool_path), "--boot_img", str(vb), "--out", str(out)])
    except subprocess.CalledProcessError as e:
        print(
            "ERROR: unpack_bootimg.py failed. Verify this tool supports vendor_boot v4 and your python env is OK.",
            file=sys.stderr,
        )
        return e.returncode or 1

    # Validate expected structure.
    by_name = out / "vendor-ramdisk-by-name"
    if not by_name.exists():
        # Try to locate it.
        cand = list(out.rglob("vendor-ramdisk-by-name"))
        if cand:
            by_name = cand[0]
        else:
            print(
                "ERROR: could not find vendor-ramdisk-by-name in output.\n"
                "Your unpack tool may have a different output layout.",
                file=sys.stderr,
            )
            return 2

    ramdisk_link = by_name / "ramdisk_"
    if ramdisk_link.exists():
        target = os.readlink(ramdisk_link) if ramdisk_link.is_symlink() else "(not a symlink)"
        print(f"[OK] found main fragment name 'ramdisk_' -> {target}")
    else:
        print(
            "WARNING: vendor-ramdisk-by-name/ramdisk_ not found.\n"
            "You might be on a different device layout; patch script will attempt fallback to vendor_ramdisk00.",
            file=sys.stderr,
        )

    print(f"[OK] unpacked to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
