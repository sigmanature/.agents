from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Tuple

from .adb_utils import adb_shell, adb_shell_retry


def is_device_awake(serial: str) -> Tuple[bool, str]:
    try:
        out = adb_shell(serial, "dumpsys power", use_su=False, timeout_s=30, check=True)
    except Exception as e:
        return False, f"ERR:{e}"

    wake_lines = [ln.strip() for ln in out.splitlines() if "mWakefulness" in ln]
    awake = any(("Awake" in ln) or ("mWakefulness=1" in ln) for ln in wake_lines)

    if wake_lines:
        summary = " | ".join(wake_lines[:4])
    else:
        summary = " | ".join(out.splitlines()[:3]).strip()
    return awake, summary


def ensure_awake_unlocked_and_stay_awake(
    serial: str,
    out_dir: Path,
    *,
    retries: int,
    retry_sleep_s: int,
) -> None:
    """Best-effort device prep for stable long-running workloads.

    - wake screen
    - attempt to dismiss keyguard
    - set 'stay on' while plugged in
    - increase screen timeout
    """

    log_path = out_dir / "device_prepare_log.txt"

    try:
        _ = adb_shell_retry(
            serial,
            "mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true",
            use_su=True,
            timeout_s=10,
            retries=1,
            retry_sleep_s=1,
        )
    except Exception:
        pass

    cmds = [
        "input keyevent KEYCODE_WAKEUP || true",
        "wm dismiss-keyguard || true",
        "input keyevent KEYCODE_MENU || true",
        "input swipe 300 1400 300 400 200 || true",
        "svc power stayon true || true",
        "settings put global stay_on_while_plugged_in 3 || true",
        "settings put system screen_off_timeout 1800000 || true",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for attempt in range(1, max(1, retries) + 1):
            f.write(f"\n[{attempt}] {datetime.now().isoformat()}\n")
            for cmd in cmds:
                f.write(f"$ {cmd}\n")
                try:
                    out = adb_shell_retry(
                        serial,
                        cmd,
                        use_su=False,
                        timeout_s=20,
                        retries=1,
                        retry_sleep_s=1,
                        tty=False,
                    )
                    if out.strip():
                        f.write(out)
                        if not out.endswith("\n"):
                            f.write("\n")
                except Exception as e:
                    f.write(f"ERR: {e}\n")

            awake, wake_out = is_device_awake(serial)
            if wake_out:
                f.write(f"wake_out={wake_out}\n")
            f.write(f"awake={awake}\n")
            f.flush()
            if awake:
                return
            if attempt < max(1, retries):
                time.sleep(max(0, retry_sleep_s))

    raise RuntimeError(
        f"device prepare failed: device did not reach awake state after {max(1, retries)} attempts"
    )

