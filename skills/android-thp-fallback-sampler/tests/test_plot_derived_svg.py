from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

import plot_derived_svg


class PlotDerivedSvgTests(unittest.TestCase):
    def test_builds_cumulative_fallback_and_ratio_series(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            derived = Path(td) / "derived.csv"
            with derived.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "window_end_host_ts",
                        "fallback_ratio",
                        "attempts",
                        "d_anon_fault_alloc",
                        "d_anon_fault_fallback",
                    ],
                )
                w.writeheader()
                w.writerow(
                    {
                        "window_end_host_ts": "100",
                        "fallback_ratio": "0.400000",
                        "attempts": "10",
                        "d_anon_fault_alloc": "6",
                        "d_anon_fault_fallback": "4",
                    }
                )
                w.writerow(
                    {
                        "window_end_host_ts": "115",
                        "fallback_ratio": "0.000000",
                        "attempts": "5",
                        "d_anon_fault_alloc": "5",
                        "d_anon_fault_fallback": "0",
                    }
                )
                w.writerow(
                    {
                        "window_end_host_ts": "130",
                        "fallback_ratio": "0.400000",
                        "attempts": "15",
                        "d_anon_fault_alloc": "9",
                        "d_anon_fault_fallback": "6",
                    }
                )

            series = plot_derived_svg.load_derived(derived, "demo", max_points=10)

            fallback_points = plot_derived_svg.build_metric_points(series, "cumulative_fallback")
            self.assertEqual(
                fallback_points,
                [(100, 4.0), (115, 4.0), (130, 10.0)],
            )

            ratio_points = plot_derived_svg.build_metric_points(series, "cumulative_ratio")
            self.assertEqual([ts for ts, _ in ratio_points], [100, 115, 130])
            self.assertAlmostEqual(ratio_points[0][1], 4 / 10, places=6)
            self.assertAlmostEqual(ratio_points[1][1], 4 / 15, places=6)
            self.assertAlmostEqual(ratio_points[2][1], 10 / 30, places=6)


if __name__ == "__main__":
    unittest.main()
