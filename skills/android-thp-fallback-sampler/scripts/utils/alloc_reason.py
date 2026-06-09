from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence


REASON_PATTERNS: List[tuple[str, Sequence[str]]] = [
    (
        "dma_heap",
        (
            "dmabuf_page_pool_alloc_pages",
            "dmabuf_page_pool_alloc",
            "system_heap_allocate",
            "alloc_largest_available",
            "dma_heap_buffer_alloc",
        ),
    ),
    (
        "gpu_mali",
        (
            "mgm_alloc_page",
            "kbase_mem_alloc_page",
            "kbase_mem_pool_alloc_pages",
            "mali_pma_alloc_page",
            "mali_pma_slab_alloc",
        ),
    ),
    ("gpu_g2d", ("g2d_create_task",)),
    ("video_codec", ("mfc_mem_dma_heap_alloc", "mfc_mem_special_buf_alloc", "smfc", "bigo", "bigo_iommu")),
    ("camera_lwis", ("lwis_platform_dma_buffer_alloc", "lwis_buffer_enroll")),
    ("wifi_skb", ("__page_frag_cache_refill", "__page_frag_alloc_align", "__netdev_alloc_skb", "linux_pktget", "dhd_msgbuf")),
    ("thp_pmd_anon", ("vma_alloc_anon_folio_pmd", "do_huge_pmd_anonymous_page")),
    ("mthp_anon", ("alloc_anon_folio", "vma_alloc_folio", "__alloc_pages_mpol_noprof", "folio_alloc_mpol_noprof")),
    ("page_cache", ("page_cache_ra_order", "filemap_alloc_folio", "__filemap_get_folio", "filemap_fault")),
    ("slab", ("allocate_slab", "___slab_alloc", "new_slab")),
    ("usb_gadget", ("ffs_epfile_io", "ffs_epfile_read", "ffs_epfile_write")),
]


def clean_stack_frame(frame: str) -> str:
    frame = frame.strip()
    frame = frame.removeprefix("=>").strip()
    frame = re.sub(r"\+0x[0-9a-fA-F]+.*$", "", frame).strip()
    return frame


def normalize_stack(stack: Iterable[str]) -> List[str]:
    return [f for f in (clean_stack_frame(x) for x in stack) if f]


def classify_stack(stack: Iterable[str]) -> str:
    text = " ".join(normalize_stack(stack))
    for reason, funcs in REASON_PATTERNS:
        if any(func in text for func in funcs):
            return reason
    return "unknown"


def classify_stack_hits(stack: Iterable[str]) -> Dict[str, int]:
    text = " ".join(normalize_stack(stack))
    return {reason: sum(1 for func in funcs if func in text) for reason, funcs in REASON_PATTERNS}
