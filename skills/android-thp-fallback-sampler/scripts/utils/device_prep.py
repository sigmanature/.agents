from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Tuple

from .adb_utils import adb_shell, adb_shell_retry


def _read_thermal_zone(serial: str, zone: str) -> float:
    import subprocess
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", f"su -c 'cat /sys/class/thermal/{zone}/temp 2>/dev/null || echo -1'"],
            capture_output=True, text=True, timeout=10,
        )
        val = out.stdout.strip()
        return float(val) / 1000.0 if val else -1.0
    except Exception:
        return -1.0


def wait_for_cool_down(
    serial: str,
    zones: list = None,
    max_temps: dict = None,
    poll_s: int = 10,
    max_wait_s: int = 1200,
    stable_samples: int = 3,
) -> dict:
    if zones is None:
        zones = ["thermal_zone0", "thermal_zone2"]
    if max_temps is None:
        max_temps = {"thermal_zone0": 50.0, "thermal_zone2": 55.0}
    t0 = time.time()
    stable_count = 0
    while True:
        temps = {}
        for z in zones:
            temps[z] = _read_thermal_zone(serial, z)
        elapsed = time.time() - t0
        parts = "  ".join(f"{z.split('_')[-1]}={temps[z]:.1f}°C" for z in zones)
        all_ok = all(temps[z] <= max_temps.get(z, 999) for z in zones) if all(t >= 0 for t in temps.values()) else False
        if all_ok:
            stable_count += 1
        else:
            stable_count = 0
        print(f"[cool_down] {parts}  stable={stable_count}/{stable_samples}  elapsed={elapsed:.0f}s", flush=True)
        if any(t < 0 for t in temps.values()):
            return {z: -1.0 for z in zones}
        if stable_count >= stable_samples:
            return temps
        if elapsed >= max_wait_s:
            print(f"[cool_down] timeout after {elapsed:.0f}s", flush=True)
            return temps
        time.sleep(poll_s)


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
    - set SELinux permissive (setenforce 0) so root sysfs writes succeed
    - lock CPU frequencies to max for stable measurements
    """

    log_path = out_dir / "device_prepare_log.txt"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmds = [
        "input keyevent KEYCODE_WAKEUP || true",
        "wm dismiss-keyguard || true",
        "input keyevent KEYCODE_MENU || true",
        "input swipe 300 1400 300 400 200 || true",
        "svc power stayon true || true",
        "settings put global stay_on_while_plugged_in 3 || true",
        "settings put system screen_off_timeout 1800000 || true",
    ]

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[cool_down] start {datetime.now().isoformat()}\n")
        temps = wait_for_cool_down(serial)
        f.write(f"[cool_down] done BIG={temps.get('thermal_zone0', -1):.1f}°C LITTLE={temps.get('thermal_zone2', -1):.1f}°C  {datetime.now().isoformat()}\n")
        f.flush()
    
        for prep_cmd, label in (
            ("setenforce 0 2>/dev/null || true", "setenforce 0"),
            # Lock CPU frequencies to max for stable measurements
            ("for i in 0 1 2 3 4 5 6 7; do "
             "maxf=$(cat /sys/devices/system/cpu/cpu$i/cpufreq/scaling_max_freq 2>/dev/null) && "
             "echo $maxf > /sys/devices/system/cpu/cpu$i/cpufreq/scaling_min_freq 2>/dev/null; "
             "done", "lock_cpu_freq"),
        ):
            f.write(f"\n[{label}] {datetime.now().isoformat()}\n")
            f.write(f"$ {prep_cmd}\n")
            try:
                out = adb_shell_retry(
                    serial,
                    prep_cmd,
                    use_su=True,
                    timeout_s=10,
                    retries=1,
                    retry_sleep_s=1,
                    tty=True,
                )
                if out.strip():
                    f.write(out)
                    if not out.endswith("\n"):
                        f.write("\n")
            except Exception as e:
                f.write(f"ERR: {e}\n")

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

