from __future__ import annotations

import json
import os
import shlex
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .adb_utils import adb_shell
from .pkg_utils import unique_preserve_order


DEFAULT_DELETE_EXTS = ("odex", "vdex", "art", "oat")


def resolve_oat_watch_packages(
    *,
    default_packages: Sequence[str],
    explicit_packages: Sequence[str],
    file_packages: Sequence[str],
) -> List[str]:
    chosen = list(explicit_packages) + list(file_packages)
    if not chosen:
        chosen = list(default_packages)
    return unique_preserve_order([x for x in chosen if x])


def dalvik_cache_patterns_for_package(pkg: str, apk_path: str) -> List[str]:
    out: List[str] = [pkg]
    apk = (apk_path or "").strip()
    if apk:
        out.append(apk.lstrip("/").replace("/", "@"))
    return unique_preserve_order([x for x in out if x])


def _name_expr(exts: Sequence[str]) -> str:
    parts = ["-name " + shlex.quote("*." + ext.lstrip(".")) for ext in exts]
    return "\\( " + " -o ".join(parts) + " \\)"


def build_device_prune_script(packages: Sequence[str], *, exts: Sequence[str] = DEFAULT_DELETE_EXTS) -> str:
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


def parse_deleted_paths(stdout: str) -> List[str]:
    return [ln.strip() for ln in (stdout or "").splitlines() if ln.strip().startswith("/")]


def prune_once(
    *,
    serial: str,
    packages: Sequence[str],
    use_su: bool,
    exts: Sequence[str] = DEFAULT_DELETE_EXTS,
    timeout_s: int = 60,
) -> Dict[str, object]:
    started = time.time()
    script = build_device_prune_script(packages, exts=exts)
    stdout = adb_shell(serial, script, use_su=use_su, timeout_s=timeout_s, tty=False, check=True)
    deleted = parse_deleted_paths(stdout)
    return {
        "serial": serial,
        "host_ts": int(started),
        "deleted_count": len(deleted),
        "deleted_paths": deleted,
        "packages": list(packages),
    }


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch_loop(
    *,
    serial: str,
    packages: Sequence[str],
    out_dir: Path,
    stop_event: threading.Event,
    poll_s: float,
    use_su: bool,
    exts: Sequence[str] = DEFAULT_DELETE_EXTS,
    until_host_pid: int = 0,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "oat_watch.jsonl"
    status_path = out_dir / "oat_watch_status.json"
    sleep_s = max(0.2, float(poll_s))

    while not stop_event.is_set():
        if until_host_pid > 0 and not _process_alive(int(until_host_pid)):
            break
        try:
            row = prune_once(serial=serial, packages=packages, use_su=use_su, exts=exts)
            row["ok"] = True
            row["poll_s"] = sleep_s
        except Exception as e:
            row = {
                "serial": serial,
                "host_ts": int(time.time()),
                "ok": False,
                "error": str(e),
                "packages": list(packages),
                "deleted_count": 0,
                "deleted_paths": [],
                "poll_s": sleep_s,
            }

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        status_path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        deadline = time.time() + sleep_s
        while not stop_event.is_set() and time.time() < deadline:
            if until_host_pid > 0 and not _process_alive(int(until_host_pid)):
                return
            time.sleep(min(0.2, max(0.0, deadline - time.time())))
