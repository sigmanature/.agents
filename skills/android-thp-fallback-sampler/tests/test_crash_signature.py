from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from utils.crash_signature import TargetCrashSignatureDetector


def _feed(detector: TargetCrashSignatureDetector, lines: list[str]):
    payload = None
    for line in lines:
        payload = detector.process_line(line)
        if payload is not None:
            return payload
    return payload


class CrashSignatureTests(unittest.TestCase):
    def test_ignores_unrelated_system_cnfe_crashes(self) -> None:
        detector = TargetCrashSignatureDetector(
            serial="SERIAL",
            target_packages={"com.google.android.GoogleCamera", "com.tencent.mm"},
            window_lines=5,
        )

        payload = _feed(
            detector,
            [
                "04-18 20:51:36.900  1452  2723 I am_crash: [4116,0,com.google.android.gms.persistent,-1,java.lang.NoSuchMethodError,foo,Bar.java,16,0]",
                "04-18 20:51:38.153  1452  1768 I am_crash: [2727,0,com.google.android.permissioncontroller,1,java.lang.ClassNotFoundException,androidx.collection.ArraySet,PerformanceTracker.java,25,0]",
            ],
        )

        self.assertIsNone(payload)

    def test_detects_target_package_cnfe_crash(self) -> None:
        detector = TargetCrashSignatureDetector(
            serial="SERIAL",
            target_packages={"com.google.android.GoogleCamera", "com.tencent.mm"},
            window_lines=5,
        )

        payload = _feed(
            detector,
            [
                "04-18 20:51:38.153  1452  1768 I am_crash: [2727,0,com.tencent.mm,1,java.lang.ClassNotFoundException,androidx.collection.ArraySet,PerformanceTracker.java,25,0]",
            ],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["matched_package"], "com.tencent.mm")
        self.assertEqual(payload["reason"], "target package am_crash + classloading error in proximity")


if __name__ == "__main__":
    unittest.main()
