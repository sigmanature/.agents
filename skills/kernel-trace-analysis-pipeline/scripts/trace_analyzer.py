#!/usr/bin/env python3
"""优化版: 用 rg 预过滤 + 流式单遍扫描。比旧版快 20x+.

用法: python3 analyze_fast.py [trace_dir] [out_json]
"""

import os
import re
import sys
import json
import subprocess
from collections import defaultdict

PAGE_16KB = 16384
COLLECT_WINDOW_US = 10000  # 10ms

# --------------- 分类器 ---------------
def is_aligned_16kb(addr: int) -> bool:
    return (addr & (PAGE_16KB - 1)) == 0


def classify(vma_start: int, vma_end: int, madv_start: int, madv_end: int):
    vma_ok = is_aligned_16kb(vma_start) and is_aligned_16kb(vma_end)
    end_ok = is_aligned_16kb(madv_end) or madv_end >= vma_end
    madv_ok = is_aligned_16kb(madv_start) and end_ok
    if not vma_ok:
        return 'mmap_vma_cause'
    elif not madv_ok:
        return 'madvise_cause'
    else:
        return 'aligned_both'


# --------------- 解析器 ---------------
EVENT_RE = re.compile(
    r'^\s*(?P<comm>[^\s-]+)-(?P<pid>\d+)\s+\[\d+\]\s+\S+\s+'
    r'(?P<ts>\d+\.\d+):\s+(?P<evt>\w+):\s+(?P<payload>.*)'
)
MADV_RE = re.compile(
    r'vma=0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)\s+start=0x([0-9a-fA-F]+)\s+len=(\d+)'
)
PARTIAL_RE = re.compile(r'folio=0x([0-9a-fA-F]+)\s+page=0x([0-9a-fA-F]+)\(off=\d+\)\s+nr=(\d+)/(\d+)')
DEFER_RE  = re.compile(r'pfn=0x([0-9a-fA-F]+)\s+order=(\d+)\s+reason=(\w+)')


# --------------- 用 rg 预过滤 ---------------
def rg_filter(trace_dir: str, event_type: str) -> str:
    """用 rg 提取指定事件的所有行，返回文本。"""
    files = sorted(
        os.path.join(trace_dir, f)
        for f in os.listdir(trace_dir)
        if f.startswith('trace_chunk_') and f.endswith('.txt')
    )
    if not files:
        return ''
    cmd = ['rg', '--no-heading', '-n', '-N', '-j', '0',
           event_type] + files
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=120, env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ''


# --------------- 流式解析 ---------------
def parse_madv_rg(text: str):
    events = []
    for line in text.splitlines():
        m = EVENT_RE.match(line)
        if not m:
            continue
        mm = MADV_RE.search(m.group('payload'))
        if not mm:
            continue
        vs = int(mm.group(1), 16)
        ve = int(mm.group(2), 16)
        ms = int(mm.group(3), 16)
        ml = int(mm.group(4))
        me = ms + ml
        events.append({
            'ts': float(m.group('ts')),
            'comm': m.group('comm'),
            'pid': int(m.group('pid')),
            'vma_start': vs, 'vma_end': ve,
            'start': ms, 'end': me, 'len': ml,
            'cat': classify(vs, ve, ms, me),
            'vma_aligned': is_aligned_16kb(vs) and is_aligned_16kb(ve),
            'madv_aligned': is_aligned_16kb(ms) and (is_aligned_16kb(me) or me >= ve),
        })
    return events


def parse_partial_rg(text: str):
    events = []
    for line in text.splitlines():
        m = EVENT_RE.match(line)
        if not m:
            continue
        pm = PARTIAL_RE.search(m.group('payload'))
        if not pm:
            continue
        events.append({
            'ts': float(m.group('ts')),
            'comm': m.group('comm'),
            'pid': int(m.group('pid')),
            'folio': int(pm.group(1), 16),
            'nr': int(pm.group(3)),
            'total': int(pm.group(4)),
        })
    return events


def parse_defer_rg(text: str):
    events = []
    for line in text.splitlines():
        m = EVENT_RE.match(line)
        if not m:
            continue
        dm = DEFER_RE.search(m.group('payload'))
        if not dm:
            continue
        order = int(dm.group(2))
        if order != 2:
            continue
        events.append({
            'ts': float(m.group('ts')),
            'comm': m.group('comm'),
            'pid': int(m.group('pid')),
            'pfn': int(dm.group(1), 16),
            'order': order,
            'reason': dm.group(3),
        })
    return events


# --------------- 关联 (流式, 时间窗口) ---------------
def correlate_window(madvs, partials, defers):
    """单遍扫描: 所有事件都时间排序, 滑动窗口 10ms 匹配."""
    # 按 (comm, pid) 建索引
    p_idx = defaultdict(list)
    d_idx = defaultdict(list)
    for e in partials:
        p_idx[(e['comm'], e['pid'])].append(e['ts'])
    for e in defers:
        d_idx[(e['comm'], e['pid'])].append(e['ts'])

    # 将 ts 列表排序 (通常已有序)
    for k in p_idx:
        p_idx[k].sort()
    for k in d_idx:
        d_idx[k].sort()

    result = defaultdict(lambda: {
        'total_madvise': 0, 'madvise_cause': 0, 'mmap_vma_cause': 0,
        'aligned_both': 0, 'triggered_partial': 0, 'triggered_split': 0,
    })

    def _has(tss, t0):
        """二分查找: tss 中是否有值在 [t0, t0+10ms] 内."""
        from bisect import bisect_left
        lo = bisect_left(tss, t0)
        return lo < len(tss) and (tss[lo] - t0) * 1e6 <= COLLECT_WINDOW_US

    for e in madvs:
        c = e['comm']
        key = (e['comm'], e['pid'])
        t0 = e['ts']
        has_p = _has(p_idx.get(key, []), t0)
        has_d = _has(d_idx.get(key, []), t0)

        result[c]['total_madvise'] += 1
        result[c][e['cat']] += 1
        if has_p:
            result[c]['triggered_partial'] += 1
        if has_d:
            result[c]['triggered_split'] += 1

    return result


# --------------- 主入口 ---------------
def main():
    trace_dir = sys.argv[1] if len(sys.argv) > 1 else './trace'
    out_json = sys.argv[2] if len(sys.argv) > 2 else None
    if not out_json:
        out_json = os.path.join(os.path.dirname(os.path.abspath(trace_dir)),
                                'analysis_result.json')

    import time
    t0 = time.time()

    print(f"[rg] extracting events from {trace_dir} ...")
    raw_madv  = rg_filter(trace_dir, 'mm_madvise_dontneed')
    raw_part  = rg_filter(trace_dir, 'mm_folio_partial_unmap')
    raw_defer = rg_filter(trace_dir, 'mm_folio_deferred_split')

    t1 = time.time()
    print(f"[rg] done in {t1-t0:.1f}s. parsing ...")

    madvs  = parse_madv_rg(raw_madv)
    parts  = parse_partial_rg(raw_part)
    defers = parse_defer_rg(raw_defer)

    t2 = time.time()
    print(f"[parse] madvise={len(madvs)}  partial={len(parts)}  defer(order2)={len(defers)}  "
          f"({t2-t1:.1f}s)")

    if not madvs:
        print("[warn] no events.")
        return

    # 分类统计
    tot_vma = sum(1 for e in madvs if e['cat'] == 'mmap_vma_cause')
    tot_mad = sum(1 for e in madvs if e['cat'] == 'madvise_cause')
    tot_ok  = sum(1 for e in madvs if e['cat'] == 'aligned_both')

    # 关联
    corr = correlate_window(madvs, parts, defers)
    tot_split   = sum(r['triggered_split']   for r in corr.values())
    tot_partial = sum(r['triggered_partial'] for r in corr.values())

    t3 = time.time()
    print(f"[correlate] {t3-t2:.1f}s")

    # ---- 输出 ----
    print(f"\n{'='*80}")
    print("MADVISE DONTNEED -> 16KB THP PARTIAL SPLIT 根因分析")
    print(f"{'='*80}\n")
    N = len(madvs)
    print(f"[事件总数]")
    print(f"  madvise_dontneed:              {N:>8}")
    print(f"  folio_partial_unmap:           {len(parts):>8}")
    print(f"  folio_deferred_split(order=2): {len(defers):>8}\n")
    print(f"[分类统计]")
    print(f"  VMA不对齐(→mmap是果):          {tot_vma:>8} ({tot_vma/N*100:.1f}%)")
    print(f"  VMA对齐+madvise不对齐(→madv因):{tot_mad:>8} ({tot_mad/N*100:.1f}%)")
    print(f"  VMA对齐+madvise也对齐:          {tot_ok:>8} ({tot_ok/N*100:.1f}%)\n")
    print(f"[关联分析 window=10ms]")
    print(f"  madvise→partial_unmap:         {tot_partial:>8} ({tot_partial/N*100:.1f}%)")
    print(f"  madvise→deferred_split:        {tot_split:>8}   ({tot_split/N*100:.1f}%)\n")

    print(f"[每进程 Top 20]")
    print(f"  {'进程':<22s}{'mad总数':>7s}{'madv因%':>8s}{'VMA因%':>8s}{'→split':>7s}")
    print(f"  {'-'*55}")
    for comm, s in sorted(corr.items(), key=lambda x: x[1]['total_madvise'], reverse=True)[:20]:
        t = s['total_madvise']
        if t < 10:
            continue
        print(f"  {comm[:22]:<22s}{t:>7d}{s['madvise_cause']/t*100:>7.1f}%"
              f"{s['mmap_vma_cause']/t*100:>7.1f}%{s['triggered_split']:>7d}")

    # 写 JSON
    result = {
        'events': {
            'madvise_dontneed': N,
            'folio_partial_unmap': len(parts),
            'folio_deferred_split_order2': len(defers),
        },
        'classification': {
            'mmap_vma_cause': tot_vma,
            'madvise_cause': tot_mad,
            'aligned_both': tot_ok,
        },
        'correlation': {
            'triggered_partial': tot_partial,
            'triggered_split': tot_split,
        },
        'per_process': {
            comm: {
                'total_madvise': s['total_madvise'],
                'madvise_cause': s['madvise_cause'],
                'mmap_vma_cause': s['mmap_vma_cause'],
                'aligned_both': s['aligned_both'],
                'triggered_split': s['triggered_split'],
                'triggered_partial': s['triggered_partial'],
            }
            for comm, s in corr.items() if s['total_madvise'] >= 10
        },
        'runtime_s': round(t3 - t0, 2),
    }
    with open(out_json, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[save] {out_json} ({t3-t0:.1f}s total)")

    print(f"\n[结论]")
    pct_madv = tot_mad / N * 100
    pct_vma  = tot_vma / N * 100
    print(f"  在 {N} 次 madvise(DONTNEED) 中:")
    print(f"  - {tot_mad}/{N} ({pct_madv:.1f}%) 是 madvise 自身不对齐导致的 partial split")
    print(f"  - {tot_vma}/{N} ({pct_vma:.1f}%) 是 mmap 创建的 VMA 不对齐导致的")
    if pct_vma > pct_madv:
        print(f"  → 大头是 VMA 边界不对齐(mmap), madvise 是果不是因。")
    else:
        print(f"  → 大头是 madvise 自身不对齐。")
    print(f"{'='*80}")


if __name__ == '__main__':
    main()