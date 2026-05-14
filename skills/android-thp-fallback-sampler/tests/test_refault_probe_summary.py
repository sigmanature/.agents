from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from summarize_refault_probe import analyze_refault, parse_victim_windows


class RefaultProbeSummaryTests(unittest.TestCase):
    def test_detects_repeated_page_keys_across_victim_revisits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trace_dir = Path(td)
            trace = trace_dir / "trace_stream_20260513_000000_0000.txt"
            trace.write_text(
                "\n".join(
                    [
                        "app-1  [001] ...1  10.000000: tracing_mark_write: memstress:victim_revisit:begin package=victim component=victim/.Main",
                        "app-1  [001] ...1  10.100000: filemap_fault_begin: dev=1:2 ino=abc pgoff=10 address=1000 flags=0 reason=0 mm=deadbeef tgid=123 comm=app",
                        "app-1  [001] ...1  10.200000: tracing_mark_write: memstress:victim_revisit:end package=victim ok=1",
                        "app-1  [001] ...1  20.000000: tracing_mark_write: memstress:victim_revisit:begin package=victim component=victim/.Main",
                        "app-1  [001] ...1  20.050000: filemap_fault_begin: dev=1:2 ino=abc pgoff=10 address=1000 flags=0 reason=0 mm=deadbeef tgid=123 comm=app",
                        "app-1  [001] ...1  20.080000: tracing_mark_write: memstress:victim_revisit:end package=victim ok=1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            windows = parse_victim_windows(trace_dir, post_window_s=1.0)
            report = analyze_refault(trace_dir, windows)

            self.assertEqual(report["victim_revisit_windows"], 2)
            self.assertTrue(report["refault_candidate"])
            self.assertEqual(report["repeated_page_keys"], 1)

    def test_supports_gzip_trace_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            trace_dir = Path(td)
            trace = trace_dir / "trace_stream_20260513_000000_0000.txt.gz"
            with gzip.open(trace, "wt", encoding="utf-8") as f:
                f.write("app-1  [001] ...1  30.000000: tracing_mark_write: memstress:victim_revisit:begin package=victim component=victim/.Main\n")
                f.write("app-1  [001] ...1  30.100000: tracing_mark_write: memstress:victim_revisit:end package=victim ok=1\n")

            windows = parse_victim_windows(trace_dir, post_window_s=0.5)
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0]["kind"], "victim_revisit")


if __name__ == "__main__":
    unittest.main()
