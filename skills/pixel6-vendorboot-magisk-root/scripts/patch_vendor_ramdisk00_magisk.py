#!/usr/bin/env python3
"""Patch Pixel 6 vendor_boot main vendor ramdisk (fragment 00) with Magisk.

This script follows the proven Magisk boot_patch.sh-style injection, but targets
vendor_ramdisk00 (the platform vendor ramdisk) and outputs a legacy-lz4
compressed fragment suitable for:
  fastboot flash vendor_boot: vendor_ramdisk.magisk.lz4

It intentionally does NOT rebuild vendor_boot.img; it only produces the patched
fragment to keep the dlkm fragment untouched.
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


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def ensure_exe(p: Path) -> None:
    if not p.exists():
        die(f"missing required file: {p}")
    if not os.access(p, os.X_OK):
        # Try to chmod +x.
        try:
            p.chmod(p.stat().st_mode | 0o111)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor-boot-out", required=True, help="Output folder produced by unpack_vendor_boot.py")
    ap.add_argument("--magisk-dir", required=True, help="Directory with magisk artifacts")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument(
        "--fragment-name",
        default="ramdisk_",
        help="By-name fragment to patch. For Pixel 6 main fragment use ramdisk_.",
    )
    args = ap.parse_args()

    vb_out = Path(args.vendor_boot_out).expanduser().resolve()
    mg = Path(args.magisk_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not vb_out.exists():
        die(f"vendor boot output dir not found: {vb_out}")

    # Tools
    lz4 = shutil.which("lz4")
    xz = shutil.which("xz")
    if not lz4:
        die("lz4 not found in PATH")
    if not xz:
        die("xz not found in PATH")

    magiskboot = mg / "magiskboot"
    magiskinit = mg / "magiskinit"
    ensure_exe(magiskboot)
    ensure_exe(magiskinit)

    magisk64_xz = mg / "magisk64.xz"
    init_ld_xz = mg / "init-ld.xz"
    stub_apk = mg / "stub.apk"

    for p in [magisk64_xz, init_ld_xz, stub_apk]:
        if not p.exists():
            die(f"missing required magisk artifact: {p}")

    # Locate fragment file
    by_name = vb_out / "vendor-ramdisk-by-name"
    if not by_name.exists():
        # fallback search
        cand = list(vb_out.rglob("vendor-ramdisk-by-name"))
        if cand:
            by_name = cand[0]
        else:
            die("could not find vendor-ramdisk-by-name in vendor boot output")

    link = by_name / args.fragment_name
    frag: Path | None = None
    if link.exists():
        # Resolve symlink target relative to link's parent
        frag = (link.parent / os.readlink(link)).resolve() if link.is_symlink() else link.resolve()
    else:
        # fallback: assume vendor_ramdisk00 in root
        cand = vb_out / "vendor_ramdisk00"
        if cand.exists():
            frag = cand
        else:
            die(f"could not find fragment '{args.fragment_name}' nor vendor_ramdisk00")

    print(f"[INFO] fragment file: {frag}")

    work = out / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # Decompress legacy lz4 -> cpio
    vr_cpio = work / "vr.cpio"
    run(["bash", "-lc", f"{lz4} -dc '{frag}' > '{vr_cpio}'"], cwd=work)

    if vr_cpio.stat().st_size < 1024:
        die("decompressed vr.cpio looks too small; is this the correct fragment?")

    # Prepare Magisk files expected by boot_patch-style injection
    magisk_xz = work / "magisk.xz"
    if not magisk_xz.exists():
        shutil.copy2(magisk64_xz, magisk_xz)

    stub_xz = work / "stub.xz"
    if not stub_xz.exists():
        run(["bash", "-lc", f"{xz} -c -9 '{stub_apk}' > '{stub_xz}'"], cwd=work)

    # init-ld.xz can be used directly
    init_ld_dst = work / "init-ld.xz"
    shutil.copy2(init_ld_xz, init_ld_dst)

    # config file for .backup/.magisk (minimal proven set)
    config = work / "config"
    config.write_text(
        "KEEPVERITY=true\nKEEPFORCEENCRYPT=true\nRECOVERYMODE=false\n",
        encoding="utf-8",
    )

    # Execute magiskboot cpio patch sequence
    cmd = [
        str(magiskboot),
        "cpio",
        str(vr_cpio),
        f"add 0750 init {magiskinit}",
        "mkdir 0750 overlay.d",
        "mkdir 0750 overlay.d/sbin",
        f"add 0644 overlay.d/sbin/magisk.xz {magisk_xz}",
        f"add 0644 overlay.d/sbin/stub.xz {stub_xz}",
        f"add 0644 overlay.d/sbin/init-ld.xz {init_ld_dst}",
        "patch",
        "backup vr.cpio.orig",
        "mkdir 000 .backup",
        f"add 000 .backup/.magisk {config}",
    ]

    try:
        run(cmd, cwd=work)
    except subprocess.CalledProcessError:
        die(
            "magiskboot cpio patch failed. Ensure your magiskboot is host-runnable (linux-x86_64) and matches your artifacts."
        )

    # Recompress to legacy lz4
    out_lz4 = out / "vendor_ramdisk.magisk.lz4"
    run(["bash", "-lc", f"lz4 -l -c '{vr_cpio}' > '{out_lz4}'"])

    print(f"[OK] wrote: {out_lz4}")
    print("[NEXT] flash with: fastboot flash vendor_boot: vendor_ramdisk.magisk.lz4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
