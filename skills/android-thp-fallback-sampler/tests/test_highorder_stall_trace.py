from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_SCRIPTS = Path("/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from trace_highorder_stalls import parse_trace, summarize_stalls
from utils.alloc_reason import classify_stack


class AllocReasonTests(unittest.TestCase):
    def test_classifies_known_driver_and_pagecache_stacks(self) -> None:
        self.assertEqual(
            classify_stack(
                [
                    "__alloc_pages_noprof",
                    "dmabuf_page_pool_alloc_pages",
                    "system_heap_allocate",
                    "dma_heap_ioctl",
                ]
            ),
            "dma_heap",
        )
        self.assertEqual(
            classify_stack(
                [
                    "__alloc_pages_noprof",
                    "page_cache_ra_order",
                    "do_sync_mmap_readahead",
                    "f2fs_filemap_fault",
                ]
            ),
            "page_cache",
        )
        self.assertEqual(
            classify_stack(
                [
                    "__folio_alloc_noprof",
                    "vma_alloc_anon_folio_pmd",
                    "do_huge_pmd_anonymous_page",
                ]
            ),
            "thp_pmd_anon",
        )
        self.assertEqual(classify_stack(["__alloc_pages_noprof", "unknown"]), "unknown")


class HighOrderStallTraceTests(unittest.TestCase):
    def test_pairs_direct_reclaim_and_compaction_with_recent_alloc_reason(self) -> None:
        raw = "\n".join(
            [
                "video-111 [001] ...1 10.000000: mm_page_alloc: page=abc pfn=1 order=9 migratetype=0 gfp_flags=GFP_HIGHUSER|__GFP_NOWARN",
                " <stack trace>",
                " => __alloc_pages_noprof",
                " => mgm_alloc_page",
                " => kbase_mem_alloc_page",
                "video-111 [001] ...1 10.001000: mm_vmscan_direct_reclaim_begin: order=9 gfp_flags=GFP_HIGHUSER|__GFP_DIRECT_RECLAIM|__GFP_IO",
                "video-111 [001] ...1 10.004000: mm_vmscan_direct_reclaim_end: nr_reclaimed=32",
                "video-111 [001] ...1 10.005000: mm_compaction_try_to_compact_pages: order=9 gfp_mask=GFP_HIGHUSER|__GFP_DIRECT_RECLAIM|__GFP_IO priority=0",
                "video-111 [001] ...1 10.006000: mm_compaction_begin: zone_start=0x1 migrate_pfn=0x2 free_pfn=0x3 zone_end=0x4, mode=sync",
                "video-111 [001] ...1 10.011000: mm_compaction_end: zone_start=0x1 migrate_pfn=0x2 free_pfn=0x3 zone_end=0x4, mode=sync status=complete",
                "kswapd0-88 [000] ...1 11.000000: mm_vmscan_direct_reclaim_begin: order=2 gfp_flags=GFP_KERNEL",
            ]
        )

        report = parse_trace(raw, reason_window_s=0.1)

        self.assertEqual(len(report.stalls), 2)
        direct = report.stalls[0]
        self.assertEqual(direct.kind, "direct_reclaim")
        self.assertEqual(direct.reason, "gpu_mali")
        self.assertEqual(direct.order, 9)
        self.assertAlmostEqual(direct.duration_ms, 3.0, places=3)
        self.assertEqual(direct.detail, "nr_reclaimed=32")

        compact = report.stalls[1]
        self.assertEqual(compact.kind, "compaction")
        self.assertEqual(compact.reason, "gpu_mali")
        self.assertEqual(compact.order, 9)
        self.assertAlmostEqual(compact.duration_ms, 5.0, places=3)
        self.assertIn("status=complete", compact.detail)

    def test_summarizes_by_kind_reason_and_order(self) -> None:
        raw = "\n".join(
            [
                "app-1 [001] ...1 1.000000: mm_page_alloc: page=abc pfn=1 order=2 migratetype=1 gfp_flags=GFP_TRANSHUGE_LIGHT|__GFP_COMP",
                " <stack trace>",
                " => __alloc_pages_mpol_noprof",
                " => alloc_anon_folio",
                "app-1 [001] ...1 1.001000: mm_vmscan_direct_reclaim_begin: order=2 gfp_flags=GFP_TRANSHUGE|__GFP_DIRECT_RECLAIM",
                "app-1 [001] ...1 1.003500: mm_vmscan_direct_reclaim_end: nr_reclaimed=4",
                "app-1 [001] ...1 2.001000: mm_vmscan_direct_reclaim_begin: order=2 gfp_flags=GFP_TRANSHUGE|__GFP_DIRECT_RECLAIM",
                "app-1 [001] ...1 2.006000: mm_vmscan_direct_reclaim_end: nr_reclaimed=8",
            ]
        )

        report = parse_trace(raw, reason_window_s=2.0)
        rows = summarize_stalls(report.stalls)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "direct_reclaim")
        self.assertEqual(rows[0]["reason"], "mthp_anon")
        self.assertEqual(rows[0]["order"], 2)
        self.assertEqual(rows[0]["count"], 2)
        self.assertAlmostEqual(rows[0]["total_ms"], 7.5, places=3)
        self.assertAlmostEqual(rows[0]["max_ms"], 5.0, places=3)


if __name__ == "__main__":
    unittest.main()
