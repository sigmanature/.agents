from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .adb_utils import adb_shell_retry


def infer_enabled_path_from_stats_dir(stats_dir: str) -> Optional[str]:
    x = stats_dir.rstrip("/")
    if not x.endswith("/stats"):
        return None
    return x[: -len("/stats")] + "/enabled"


def ensure_thp_mode_for_stats(
    serial: str,
    *,
    stats_dir: str,
    use_su: bool,
    desired_mode: str,
    retries: int,
    retry_sleep_s: int,
    log_path: Path,
) -> Dict[str, str]:
    """Ensure THP mode for the stats directory's sibling `enabled` file.

    Returns a result dict suitable for run_manifest.json.
    """

    result: Dict[str, str] = {
        "stats_dir": stats_dir,
        "status": "skipped",
        "enabled_path": "",
        "before": "",
        "after": "",
        "desired": desired_mode,
        "reason": "",
    }

    enabled_path = infer_enabled_path_from_stats_dir(stats_dir)
    if not enabled_path:
        result["reason"] = "stats_dir does not end with /stats"
        return result

    result["enabled_path"] = enabled_path

    def _read_enabled() -> str:
        out = adb_shell_retry(
            serial,
            f"cat {enabled_path}",
            use_su=use_su,
            timeout_s=20,
            retries=max(0, retries),
            retry_sleep_s=max(0, retry_sleep_s),
            tty=use_su,
        )
        return out.strip()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] stats_dir={stats_dir} enabled_path={enabled_path}\n")
        before = _read_enabled()
        result["before"] = before
        f.write(f"before: {before}\n")

        desired = (desired_mode or "").strip().lower()
        if not desired or desired == "none":
            result["status"] = "checked"
            result["after"] = before
            result["reason"] = "desired mode is none"
            f.write("desired mode is none; check-only\n")
            return result

        cmd = f"echo {shlex.quote(desired)} > {enabled_path}"
        adb_shell_retry(
            serial,
            cmd,
            use_su=use_su,
            timeout_s=20,
            retries=max(0, retries),
            retry_sleep_s=max(0, retry_sleep_s),
            tty=use_su,
        )

        after = _read_enabled()
        result["after"] = after
        f.write(f"after: {after}\n")

        if f"[{desired}]" not in after and desired not in after.split():
            result["status"] = "failed"
            result["reason"] = "desired mode not active after write"
            raise RuntimeError(
                f"THP mode ensure failed for {enabled_path}: expected '{desired}' active, got '{after}'"
            )

        result["status"] = "ensured"
        result["reason"] = "ok"
        return result

