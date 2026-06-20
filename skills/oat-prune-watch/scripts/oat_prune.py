#!/usr/bin/env python3
"""Android OAT/VDEX/ART artifact pruning daemon.

Usage:
  python3 oat_prune.py --serial <SERIAL> --packages pkg1 pkg2 ... [--poll-s 2.0] [--out-dir /tmp/oat_watch]
  python3 oat_prune.py --serial <SERIAL> --all-packages [--poll-s 5.0]

Delete compiled runtime artifacts (.odex/.vdex/.art/.oat) of target packages,
forcing ART to recompile on each launch.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_DELETE_EXTS = ("odex", "vdex", "art", "oat")


# --------------- adb helpers ---------------

def adb_shell_cp(serial: str, cmd: str, timeout_s: int = 60, check: bool = True,
                 use_su: bool = False) -> subprocess.CompletedProcess:
    if use_su:
        cmd = f"su -c {shlex.quote(cmd)}"
    full = ["adb", "-s", serial, "shell", cmd]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout_s)


def adb_stdout(serial: str, cmd: str, timeout_s: int = 60, use_su: bool = False) -> str:
    cp = adb_shell_cp(serial, cmd, timeout_s=timeout_s, check=False, use_su=use_su)
    return (cp.stdout or "").strip()


def list_packages(serial: str) -> List[str]:
    out = adb_stdout(serial, "pm list packages -3", timeout_s=30)
    pkgs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line.split(":", 1)[1])
    return pkgs


# --------------- prune logic ---------------

def _name_expr(exts: Sequence[str]) -> str:
    parts = ["-name " + shlex.quote("*." + ext.lstrip(".")) for ext in exts]
    return "\\( " + " -o ".join(parts) + " \\)"


def _build_prune_script(packages: Sequence[str], exts: Sequence[str] = DEFAULT_DELETE_EXTS) -> str:
    pkg_lines = "\n".join(shlex.quote(pkg) for pkg in packages)
    ext_expr = _name_expr(exts)
    return f"""set -eu
tmp_pkgs=$(mktemp)
cat > "$tmp_pkgs" <<'EOF_PKGS'
{pkg_lines}
EOF_PKGS
while IFS= read -r pkg; do
  [ -n "$pkg" ] || continue
  base=$(pm path "$pkg" | sed -n 's/^package://p' | head -n1)
  [ -n "$base" ] || continue
  dir=$(dirname "$base")
  if [ -d "$dir/oat" ]; then
    find "$dir/oat" -type f {ext_expr} ! -name '*.tmp' -print -delete 2>/dev/null || true
  fi
  cache_key=$(printf '%s' "$base" | sed 's#^/##; s#/#@#g')
  find /data/dalvik-cache -maxdepth 3 -type f {ext_expr} ! -name '*.tmp' \\( -name "*$pkg*" -o -name "*$cache_key*" \\) -print -delete 2>/dev/null || true
done < "$tmp_pkgs"
rm -f "$tmp_pkgs"
"""


def prune_once(serial: str, packages: Sequence[str], use_su: bool,
               exts: Sequence[str] = DEFAULT_DELETE_EXTS,
               timeout_s: int = 60) -> dict:
    started = time.time()
    script = _build_prune_script(packages, exts=exts)
    cp = adb_shell_cp(serial, script, timeout_s=timeout_s, check=True, use_su=use_su)
    deleted = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip().startswith("/")]
    return {
        "serial": serial, "host_ts": int(started),
        "deleted_count": len(deleted), "deleted_paths": deleted,
        "packages": list(packages),
    }


def watch_loop(serial: str, packages: Sequence[str], out_dir: Path,
               poll_s: float, use_su: bool,
               stop_event: threading.Event,
               exts: Sequence[str] = DEFAULT_DELETE_EXTS):
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "oat_watch.jsonl"
    status_path = out_dir / "oat_watch_status.json"
    sleep_s = max(0.2, float(poll_s))

    while not stop_event.is_set():
        try:
            row = prune_once(serial, packages, use_su=use_su, exts=exts)
            row["ok"] = True
            row["poll_s"] = sleep_s
        except Exception as e:
            row = {
                "serial": serial, "host_ts": int(time.time()),
                "ok": False, "error": str(e),
                "packages": list(packages),
                "deleted_count": 0, "deleted_paths": [], "poll_s": sleep_s,
            }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        status_path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stop_event.wait(sleep_s)


# --------------- CLI ---------------

def main():
    p = argparse.ArgumentParser(description="Android OAT/VDEX/ART artifact pruning daemon")
    p.add_argument("--serial", required=True, help="Target device serial")
    p.add_argument("--packages", nargs="*", default=[], help="Target packages")
    p.add_argument("--package-file", help="File with one package per line")
    p.add_argument("--all-packages", action="store_true", help="Prune all installed 3rd-party packages")
    p.add_argument("--poll-s", type=float, default=2.0, help="Poll interval in seconds (default: 2.0)")
    p.add_argument("--out-dir", required=True, help="Output directory")
    p.add_argument("--use-su", action="store_true", help="Use su -c for shell commands")
    p.add_argument("--exts", default="odex,vdex,art,oat", help="File extensions to delete (default: odex,vdex,art,oat)")
    args = p.parse_args()

    packages = list(args.packages)
    if args.package_file:
        packages.extend(Path(args.package_file).read_text().splitlines())
    if args.all_packages:
        packages = list(set(packages + list_packages(args.serial)))
    packages = sorted(set(p for p in packages if p))

    if not packages:
        print("[error] no packages specified", file=sys.stderr)
        return 1

    exts = tuple(e.strip().lstrip(".") for e in args.exts.split(",") if e.strip())
    out_dir = Path(args.out_dir)

    print(f"[oat-prune] serial={args.serial} packages={len(packages)} poll_s={args.poll_s}")
    print(f"[oat-prune] exts={exts} out_dir={out_dir}")

    stop = threading.Event()

    import signal
    def _handler(sig, frame):
        print("\n[oat-prune] stopping...")
        stop.set()
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    watch_loop(args.serial, packages, out_dir, args.poll_s, args.use_su, stop, exts=exts)
    print("[oat-prune] stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())