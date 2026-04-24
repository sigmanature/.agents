from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from utils.oat_watch import (
    build_device_prune_script,
    dalvik_cache_patterns_for_package,
    resolve_oat_watch_packages,
)


class OatWatchTests(unittest.TestCase):
    def test_collects_data_app_and_dalvik_cache_targets_but_skips_tmp(self) -> None:
        script = build_device_prune_script(
            ["com.tencent.mm", "com.google.android.GoogleCamera"],
            exts=("odex", "vdex", "art"),
        )

        self.assertIn('find "$dir/oat"', script)
        self.assertIn("/data/dalvik-cache", script)
        self.assertIn("com.tencent.mm", script)
        self.assertIn("com.google.android.GoogleCamera", script)
        self.assertIn("! -name '*.tmp'", script)
        self.assertIn("*.odex", script)
        self.assertIn("*.vdex", script)
        self.assertIn("*.art", script)

    def test_builds_dalvik_cache_patterns_from_package_and_apk_path(self) -> None:
        pats = dalvik_cache_patterns_for_package(
            "com.google.android.GoogleCamera",
            "/product/priv-app/GoogleCamera/GoogleCamera.apk",
        )

        self.assertIn("com.google.android.GoogleCamera", pats)
        self.assertIn("product@priv-app@GoogleCamera@GoogleCamera.apk", pats)

    def test_defaults_to_runner_packages_when_no_override_is_provided(self) -> None:
        pkgs = resolve_oat_watch_packages(
            default_packages=["com.tencent.mm", "com.UCMobile"],
            explicit_packages=[],
            file_packages=[],
        )

        self.assertEqual(pkgs, ["com.tencent.mm", "com.UCMobile"])


if __name__ == "__main__":
    unittest.main()
