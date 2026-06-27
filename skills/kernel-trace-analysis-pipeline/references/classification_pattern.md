# Trace 归因分类模式

## 核心原则

1. **互斥**：每个事件归入恰好一个分类
2. **完备**：分类覆盖所有事件，总和 = 总事件数
3. **可追溯**：每个分类的判定条件基于 trace 中可观测的字段
4. **可举例**：能给出真实 trace 行作为该分类的例证

## 标准模板

```python
def classify(event):
    """将事件归因到根因分类。"""
    if not condition_A(event):
        return 'A类'       # 不符合条件A → 归因到A
    elif not condition_B(event):
        return 'B类'       # 符合A但不符合B → 归因到B
    return '正常'           # 都符合 → 正常
```

## 实战案例：madvise 16KB 不对齐归因

```python
PAGE_16KB = 16384

def classify(vma_start, vma_end, madv_start, madv_end):
    vma_ok = (vma_start & (PAGE_16KB-1)) == 0 and \
             (vma_end   & (PAGE_16KB-1)) == 0
    madv_ok = (madv_start & (PAGE_16KB-1)) == 0 and \
              ((madv_end & (PAGE_16KB-1)) == 0 or madv_end >= vma_end)

    if not vma_ok:
        return 'mmap_vma_cause'     # VMA边界不齐 → mmap的锅
    elif not madv_ok:
        return 'madvise_cause'      # VMA齐但madvise不齐 → madvise的锅
    return 'aligned_both'           # 都齐 → 不可能partial split
```

### 判定条件可追溯性

| 条件 | trace 字段 | 示例 |
|---|---|---|
| VMA不对齐 | `vma=0x7d8107d000-0x7d8108e000` | start & 0x3FFF = 0x1000 ≠ 0 |
| madvise不对齐 | `start=0x7ac0022000` | start & 0x3FFF = 0x2000 ≠ 0 |

### 归因递减验证

分类后必须验证：
1. 三类之和 = 总事件数 ✓
2. 给出每类一个真实 trace 例子 ✓
3. 解释为什么该类属于该归因（用 trace 中的地址偏移值）✓

## 递归拆解模式

当一级分类不够细时，对某一类做二级拆解：

```
一级分类: VMA不对齐 (78.4%)
  └→ 二级: start不对齐(end对齐): 8852
  └→ 二级: end不对齐(start对齐): 10394  
  └→ 二级: start+end都不对齐: 63753
```

拆分依据：
- start对齐判定：`vma_start & 0x3FFF == 0`
- end对齐判定：`vma_end & 0x3FFF == 0`

## 时间窗口关联

```python
from bisect import bisect_left

def correlate(event_A_list, event_B_list, window_us=10000):
    """
    事件A发生后 window_us 微秒内，同一进程是否触发了事件B。
    复杂度: O(n log m)，不是 O(n*m)
    """
    b_by_pid = {}  # (comm, pid) → sorted ts list
    for b in event_B_list:
        key = (b['comm'], b['pid'])
        b_by_pid.setdefault(key, []).append(b['ts'])
    for ts_list in b_by_pid.values():
        ts_list.sort()

    result = []
    for a in event_A_list:
        key = (a['comm'], a['pid'])
        tss = b_by_pid.get(key, [])
        lo = bisect_left(tss, a['ts'])
        if lo < len(tss) and (tss[lo] - a['ts']) * 1e6 <= window_us:
            result.append((a, tss[lo]))
    return result
```

## 每进程拆解输出

```
  进程                 总mad  madv因%   VMA因% →split
  HeapTaskDaemon      29,025    7.6%   86.3%  21,749
  ReferenceQueueD      6,687   12.8%   86.4%   5,539
  ...
  其他进程            87,126    9.3%   88.2%  18,248
```

要求：
- Top 5 进程单独列出
- 剩余进程合并为 "其他进程"
- 每个进程给出 madvise总数、各分类占比、触发 split 次数

## 因果链追溯：必须保留代码行

### 原则

分析输出不能只写 "A 调用了 B 导致 C" 的文字描述。必须附带：

1. **具体代码行号** — `mm/madvise.c:872` 不是 "madvise 函数里"
2. **调用链片段** — 展示关键函数调用关系
3. **文件路径** — 完整相对路径

### 正确格式

```
因果链: madvise(DONTNEED) → partial unmap → deferred split
  1. mm/madvise.c:872  madvise_dontneed_single_vma()
     → zap_page_range_single_batched()
  2. mm/rmap.c:xxx  folio_remove_rmap_ptes()
     → 检测 partial unmap → deferred_split_folio(folio, true)
  3. mm/huge_memory.c:4103  deferred_split_folio()
     → list_add_tail(&folio->_deferred_list, &ds_queue->split_queue)
     → trace_mm_folio_deferred_split(folio, 0)
  4. mm/vmscan.c  deferred_split_scan shrinker 回调 → 实际拆分
```

### 禁止做法

- ✗ "ART GC 调用 madvise 导致 split"
- ✗ "在 madvise 函数中触发"
- ✓ `mm/madvise.c:872 madvise_dontneed_single_vma() → zap_page_range_single_batched()`

## 多方指标交叉校验

### 目的

用多个独立指标相互校验，发现数据统计 bug 或分析逻辑错误。

### 必做校验项

| 校验 | 公式 | 不通过则说明 |
|---|---|---|
| 分类完备性 | 各分类之和 = 总事件数 | 有事件漏分类或重复计数 |
| 关联合理性 | triggered ≤ 总事件数 | 关联逻辑有 bug |
| 进程聚合 | 各进程之和 = 总事件数 | per_process 聚合遗漏 |
| split 比率上限 | triggered_split ≤ defer 事件数 | 一个 madvise 可触发多个 split 但不会超过总 split 数 |
| partial vs split | partial ≥ split（通常 3-10x） | 正常：一个 folio 多次 partial 只入队一次 split |
| per_process vs classification | 各进程各分类之和 = 全局各分类之和 | 分类逻辑不一致 |

### 交叉校验代码模板

```python
# 在分析脚本末尾添加
def validate(result):
    N = result['events']['madvise_dontneed']
    cls = result['classification']
    pp = result['per_process']
    
    # 1. 分类完备性
    cls_sum = cls['mmap_vma_cause'] + cls['madvise_cause'] + cls['aligned_both']
    assert cls_sum == N, f"分类和{cls_sum} != 总数{N}"
    
    # 2. 进程聚合
    pp_sum = sum(s['total_madvise'] for s in pp.values())
    assert pp_sum == N, f"进程和{pp_sum} != 总数{N}"
    
    # 3. split 不超总 defer 数
    triggered = result['correlation']['triggered_split']
    defers = result['events']['folio_deferred_split_order2']
    # 一个 split 可被多个 madvise 触发 (都落在同 10ms 窗口), 所以 triggered 可 >= defers
    # 但不应超过 N
    assert triggered <= N, f"triggered_split{triggered} > 总madvise{N}"
    
    # 4. partial / split 比率检查
    partials = result['events']['folio_partial_unmap']
    ratio = partials / max(1, defers)
    if ratio < 1:
        print(f"[WARN] partial({partials}) < split({defers}), 比例异常")
    
    print(f"[validate] PASS: cls_sum={cls_sum} pp_sum={pp_sum} "
          f"triggered={triggered} defers={defers} partial/split={ratio:.1f}x")

validate(result)
```

### 常见数据异常及处理

| 异常 | 可能原因 | 处理 |
|---|---|---|
| 分类和 ≠ 总数 | 逻辑 bug 或事件格式变化 | 打印差异事件的 trace 行 |
| per_process 和 ≠ 总数 | 有进程 madvise < MIN_EVENTS 被过滤 | 调低阈值或增加 "其他" 项 |
| triggered > 总 defer | 多个 madvise 命中同一 folio（正常） | 确认比例合理（通常 1-3x） |
| partial / split < 3 | split 队列被异常清空 | 检查 shrinker 行为 |
| 两轮 madvise 总量差异 > 50% | 设备状态不同（app 数量/内存） | 重启后确保等量 PM 加载后开测 |
