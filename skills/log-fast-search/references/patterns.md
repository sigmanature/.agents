# 大日志常见分析模式

## 1. rg 预过滤模式

### 提取多种事件

```bash
# 从 300MB trace 中只提取 3 种事件行
rg --no-heading -N -j 0 \
  'mm_madvise_dontneed|mm_folio_deferred_split|mm_folio_partial_unmap' \
  trace_chunk_*.txt > filtered.txt
```

### 统计事件数量 (无 Python)

```bash
# 各事件出现次数
rg -c 'mm_madvise_dontneed' trace_chunk_*.txt
rg -c 'mm_folio_deferred_split.*order=2' trace_chunk_*.txt
rg -c 'mm_folio_partial_unmap' trace_chunk_*.txt

# 按进程名分组统计
rg -oP '^\s*\K[^\s-]+(?=-\d+\s+).*mm_madvise_dontneed' trace_chunk_*.txt \
  | cut -d' ' -f1 | sort | uniq -c | sort -rn | head -20
```

### 提取特定进程

```bash
rg 'HeapTaskDaemon.*mm_madvise_dontneed' trace_chunk_*.txt > heap_madv.txt
```

## 2. 时间窗口关联模式 (bisect)

### 问题: "事件A之后 10ms 内是否发生了事件B"

标准解法:

```python
from bisect import bisect_left
from collections import defaultdict

# 1. 按 (comm, pid) 建事件B的时间戳索引
b_idx = defaultdict(list)
for b_event in b_events:
    b_idx[(b_event['comm'], b_event['pid'])].append(b_event['ts'])
for k in b_idx:
    b_idx[k].sort()

# 2. 对每个A事件, 二分查找B
def has_b_within_window(a_event, window_us=10000):
    key = (a_event['comm'], a_event['pid'])
    tss = b_idx.get(key, [])
    lo = bisect_left(tss, a_event['ts'])
    return lo < len(tss) and (tss[lo] - a_event['ts']) * 1e6 <= window_us
```

### 时间复杂度

| 方法 | 复杂度 | 100K A × 100K B |
|---|---|---|
| 嵌套循环 `for a in A: for b in B:` | O(n²) | 10^10 次比较 = 超时 |
| bisect + 索引 | O(n×log m) | 100K × log(100K) ≈ 1.7M = <1s |

## 3. 滑动窗口关联模式

### 问题: "在时间线中, 事件A前后各 5ms 内的事件B分布"

适用于所有事件都已经按时间排序的场景:

```python
def sliding_window_match(events_a, events_b, window_us=5000):
    """事件B的时间戳列表已排序。在A事件前后 window 内找B。"""
    from bisect import bisect_left, bisect_right
    b_ts = [e['ts'] for e in events_b]  # 预设已排序

    results = []
    for a in events_a:
        t0 = a['ts']
        lo = bisect_left(b_ts, t0 - window_us / 1e6)
        hi = bisect_right(b_ts, t0 + window_us / 1e6)
        matched = [events_b[i] for i in range(lo, hi)]
        if matched:
            results.append((a, matched))
    return results
```

## 4. 大数据聚合模式

### 按进程名/线程名聚合

```bash
# 哪种进程产生的 partial_unmap 最多?
rg 'mm_folio_partial_unmap' trace_chunk_*.txt \
  | rg -oP '^\s*\K[^\s-]+' | sort | uniq -c | sort -rn | head -20
```

### 数值分布统计

```bash
# partial_unmap 的 nr 值分布
rg -oP 'nr=\K\d+' trace_chunk_*.txt | sort -n | uniq -c | sort -rn | head -20
```

### 时间分布

```bash
# 每秒事件密度
rg 'mm_madvise_dontneed' trace_chunk_*.txt \
  | rg -oP '\d+\.\d+:' | cut -d. -f1 | sort -n | uniq -c
```

## 5. 内存安全原则

### 绝对不要做的事

```python
# ❌ 炸内存: 300MB 文件全读
with open('trace.txt') as f:
    text = f.read()

# ❌ 炸内存: 300MB × 正则回溯
re.findall(huge_pattern, text)

# ❌ 炸 CPU: O(n²) 嵌套
for e1 in events:
    for e2 in events:
        if abs(e1['ts'] - e2['ts']) < 0.01:
            match(e1, e2)

# ❌ 慢: grep 大文件
grep pattern 300MB_trace.txt
```

### 正确做法

```python
# ✅ rg 预过滤
text = subprocess.run(['rg', pattern, trace_file], capture_output=True).stdout

# ✅ 流式解析
for line in text.splitlines():
    parse(line)

# ✅ bisect 关联
from bisect import bisect_left

# ✅ rg 替代 grep
```

## 6. Python 调 rg 的坑

```python
# ❌ 默认环境可能带 locale 延迟
subprocess.run(['rg', pattern, file], ...)

# ✅ 显式设置 C locale, 去 unicode 开销
subprocess.run(['rg', pattern, file], env={**os.environ, 'LC_ALL': 'C'})

# ❌ rg 大文件可能超时  
subprocess.run(..., timeout=30)

# ✅ 设置合理超时 (rg 通常很快)
subprocess.run(..., timeout=120)
```