from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Optional


class TargetCrashSignatureDetector:
    """Detect target-package classloading crashes from logcat lines.

    We intentionally ignore unrelated system/package crashes. For this workload,
    the stop condition should only trip when a selected memstress target package
    hits an `am_crash` near a classloading failure.
    """

    def __init__(self, *, serial: str, target_packages: Iterable[str], window_lines: int = 500) -> None:
        self.serial = serial
        self.target_packages = set(target_packages)
        self.window_lines = max(1, int(window_lines))
        self.context: Deque[str] = deque(maxlen=200)
        self.cnfe_re = re.compile(r"(ClassNotFoundException|NoClassDefFoundError|ClassNotFoundError)")
        self.am_crash_re = re.compile(r"\bam_crash:\s*\[[^,]+,[^,]+,([^,\]]+),")
        self.win_target_am_crash = 0
        self.last_target_package = ""

    def process_line(self, line: str) -> Optional[dict]:
        s = line.rstrip("\n")
        self.context.append(s)

        saw_cnfe = self.cnfe_re.search(s) is not None
        matched_pkg = self._extract_am_crash_package(s)
        saw_target_am_crash = bool(matched_pkg and matched_pkg in self.target_packages)

        if saw_target_am_crash:
            self.win_target_am_crash = self.window_lines
            self.last_target_package = matched_pkg or ""

        confirmed = (saw_target_am_crash and saw_cnfe) or (saw_cnfe and self.win_target_am_crash > 0)
        payload = None
        if confirmed:
            payload = {
                "serial": self.serial,
                "host_ts": int(time.time()),
                "reason": "target package am_crash + classloading error in proximity",
                "window_lines": self.window_lines,
                "matched_line": s,
                "matched_package": matched_pkg or self.last_target_package,
                "context_tail": list(self.context),
            }

        if self.win_target_am_crash > 0:
            self.win_target_am_crash -= 1

        return payload

    def write_payload(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _extract_am_crash_package(self, line: str) -> str:
        m = self.am_crash_re.search(line)
        if not m:
            return ""
        return m.group(1).strip()
