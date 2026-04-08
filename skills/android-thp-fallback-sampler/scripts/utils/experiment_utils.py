from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from .adb_utils import adb_shell, run


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_out_dir(out_dir: Optional[str], *, default_prefix: str) -> Path:
    p = Path(out_dir) if out_dir else Path("output") / f"{default_prefix}_{now_ts()}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_setup_cmds(serial: str, setup_cmds: Sequence[str], *, use_su: bool, log_path: Path) -> None:
    if not setup_cmds:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for i, cmd in enumerate(setup_cmds, start=1):
            f.write(f"[{i}] {cmd}\n")
            try:
                out = adb_shell(serial, cmd, use_su=use_su, timeout_s=30, tty=use_su, check=True)
                if out.strip():
                    f.write(out)
                    if not out.endswith("\n"):
                        f.write("\n")
            except Exception as e:
                f.write(f"ERROR: {e}\n")


def maybe_install_apks(*, scripts_dir: Path, apk_dir: Optional[str], serial: str, out_dir: Path) -> Optional[Path]:
    if not apk_dir:
        return None

    apk_dir_path = Path(apk_dir)
    if not apk_dir_path.exists():
        raise FileNotFoundError(f"apk dir not found: {apk_dir}")

    installer = scripts_dir / "apk_batch_install.py"
    install_out = out_dir / "apk_install"
    install_out.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(installer), str(apk_dir_path), "--serial", serial, "--output-dir", str(install_out)]
    cp = run(cmd, timeout_s=60 * 60, check=False)
    (install_out / "installer_stdout.txt").write_text(cp.stdout or "", encoding="utf-8")
    (install_out / "installer_stderr.txt").write_text(cp.stderr or "", encoding="utf-8")
    if cp.returncode != 0:
        raise RuntimeError(f"apk_batch_install failed rc={cp.returncode}. See {install_out}/installer_stderr.txt")
    return install_out

