#!/usr/bin/env python3
"""
rg_parse.py — 可复用的大日志 rg 预过滤 + 流式解析 + bisect 关联模板。

快速入门:
  1. 修改底部的 EVENT_RE, parse_*(), classify()
  2. 修改 rg_filter() 中的事件关键词
  3. 修改 COLLECT_WINDOW_US 时间窗口
  4. 运行: python3 rg_parse.py <trace_dir> <output.json>

依赖: rg (ripgrep), Python 3.8+
"""

import os
import re
import sys
import json
import subprocess
from bisect import bisect_left
from collections import defaultdict
from typing import List, Dict, Tuple

# --------------- 配置区 (按需修改) ---------------

COLLECT_WINDOW_US = 10000   # 时间关联窗口, 微秒
MIN_EVENTS = 10             # 最少事件数才写入 per_process

# ftrace trace_pipe 标准行格式
EVENT_RE = re.compile(
    r'^\s*(?P<comm>[^\s-]+)-(?P<pid>\d+)\s+\[\d+\]\s+\S+\s+'
    r'(?P<ts>\d+\.\d+):\s+(?P<evt>\w+):\s+(?P<payload>.*)'
)

# 事件类型关键词 (传给 rg 做预过滤)
EVENT_KEYWORDS = [
    'mm_madvise_dontneed',
    'mm_folio_partial_unmap',
    'mm_folio_deferred_split',
]

# --------------- 工具函数 ---------------

def rg_filter(trace_dir: str, keywords: List[str] = None,
              file_glob: str = 'trace_chunk_*.txt') -> str:
    """用 rg 提取包含指定关键字的行。返回合并文本。

    不加载全文件到内存，rg 自身做并行搜索。
    """
    if keywords is None:
        keywords = EVENT_KEYWORDS

    files = sorted(
        os.path.join(trace_dir, f)
        for f in os.listdir(trace_dir)
        if f.startswith(file_glob.replace('*', '').split('*')[0]) and f.endswith('.txt')
    )
    if not files:
        print(f"[warn] no files matching {file_glob} in {trace_dir}")
        return ''

    cmd = ['rg', '--no-heading', '-N', '-j', '0'] + keywords + files
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=120, env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[error] rg timed out on {len(files)} files")
        return ''
    except FileNotFoundError:
        print("[error] rg not installed. install: apt install ripgrep  or  cargo install ripgrep")
        return ''


def has_ts_in_window(sorted_ts: List[float], target: float,
                     window_us: int = None) -> bool:
    """二分查找 sorted_ts 是否在 [target, target+window_us] 内有值。"""
    if not sorted_ts:
        return False
    win = window_us or COLLECT_WINDOW_US
    lo = bisect_left(sorted_ts, target)
    return lo < len(sorted_ts) and (sorted_ts[lo] - target) * 1e6 <= win


# --------------- 解析器 (按你的 trace 格式修改) ---------------

def parse_event_line(line: str) -> dict or None:
    """解析一行 trace 为 dict。按需修改字段提取逻辑。"""
    m = EVENT_RE.match(line)
    if not m:
        return None
    return {
        'ts': float(m.group('ts')),
        'comm': m.group('comm'),
        'pid': int(m.group('pid')),
        'event': m.group('evt'),
        'payload': m.group('payload'),
    }


# --------------- 关联引擎 ---------------

def correlate_by_window(primary: List[dict],
                        secondary_ts_lists: List[Tuple[str, List[Dict]]],
                        window_us: int = None):
    """
    primary: 主事件列表, 每个有 ts, comm, pid
    secondary_ts_lists: [(名称, 按 (comm,pid) 分组的 ts 列表), ...]

    Returns:
        per_primary_key -> { total, triggered_by_<name1>, triggered_by_<name2>, ... }
    """
    win = window_us or COLLECT_WINDOW_US

    # 建次级事件索引
    ts_indices = {}
    for name, events in secondary_ts_lists:
        idx = defaultdict(list)
        for e in events:
            idx[(e['comm'], e['pid'])].append(e['ts'])
        for k in idx:
            idx[k].sort()
        ts_indices[name] = idx

    result = defaultdict(lambda: {'total': 0})
    for e in primary:
        key = e.get('key', e.get('comm', 'unknown'))
        pid_key = (e['comm'], e['pid'])
        result[key]['total'] += 1
        for name, idx in ts_indices.items():
            tss = idx.get(pid_key, [])
            if has_ts_in_window(tss, e['ts'], win):
                col = f'triggered_{name}'
                result[key][col] = result[key].get(col, 0) + 1
    return result


# --------------- 流式分类器 ---------------

def classify_and_count(events: List[dict], classify_fn) -> Dict:
    """流式: 遍历一次, 分类 + 计数。"""
    counts = defaultdict(lambda: {'total': 0})
    for e in events:
        c = e.get('key', e.get('comm', 'unknown'))
        cat = classify_fn(e)
        counts[c]['total'] += 1
        counts[c][cat] = counts[c].get(cat, 0) + 1
    return counts


# --------------- 使用示例 ---------------

def example_classifier(event: dict):
    """示例: 16KB 对齐判定. 替换为你自己的分类逻辑."""
    # 这里只是骨架, 实际分类逻辑由调用方定义
    return 'example'


def build_result_json(primary_count, secondary_counts, per_primary,
                      runtime_s: float) -> dict:
    """构建标准 JSON 输出."""
    return {
        'events': {'primary': primary_count, **secondary_counts},
        'per_process': {
            k: v for k, v in per_primary.items()
            if v.get('total', 0) >= MIN_EVENTS
        },
        'runtime_s': round(runtime_s, 2),
    }


def save_json(data: dict, path: str):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[save] {path}")


# ================ 可直接运行的 DEMO ================

if __name__ == '__main__':
    import time

    trace_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    out_file = sys.argv[2] if len(sys.argv) > 2 else './rg_parse_output.json'

    t0 = time.time()

    # 1. rg 提取
    print(f"[rg] scanning {trace_dir} ...")
    text = rg_filter(trace_dir)
    t1 = time.time()
    print(f"[rg] {t1-t0:.1f}s, got {len(text.splitlines())} lines")

    # 2. 解析
    events = []
    for line in text.splitlines():
        evt = parse_event_line(line)
        if evt:
            events.append(evt)
    t2 = time.time()
    print(f"[parse] {len(events)} events ({t2-t1:.1f}s)")

    # 3. 按事件类型分组
    from collections import Counter
    evt_types = Counter(e['event'] for e in events)
    for et, cnt in evt_types.most_common():
        print(f"  {et}: {cnt}")

    # 4. 输出
    result = {
        'total_events': len(events),
        'event_types': dict(evt_types),
        'runtime_s': round(t2 - t0, 2),
    }
    save_json(result, out_file)
    print(f"[done] {t2-t0:.1f}s total")