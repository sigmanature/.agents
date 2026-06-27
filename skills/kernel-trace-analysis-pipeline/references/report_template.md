# 报告规范

## 指标命名

| 中文名 | 对应 trace 事件 | 英文缩写（禁止在报告正文使用） |
|---|---|---|
| madvise 释放调用 | `mm_madvise_dontneed` | madvise |
| 部分解映射 | `mm_folio_partial_unmap` | partial unmap |
| 延迟拆分 | `mm_folio_deferred_split` | deferred split |
| VMA不对齐(非MAP_FIXED) | VMA边界不以16KB为界 | VMA misalignment |
| madvise不对齐 | madvise起始地址不以16KB为界 | madvise misalignment |
| 触发率 | 有关联的事件B数/事件A总数 | trigger rate |

规则：
- 正文首次出现用 **中文全称（trace事件名）**，如 "延迟拆分次数（mm_folio_deferred_split）"
- 图表轴标签、表头用中文
- 百分比附分子分母：78.4%（59046/75315）

## 报告结构

### 1. 实验环境与配置
```
设备: Pixel 6 (18281FDF6007HB), 内存 7.7GB
内核: 6.18.0 + 自定义 tracepoint  
THP: 16KB=always, 其他=never, defrag=always
Workload: memstress, 120 轮, seed=20260617, burst=1
采集事件: mm_madvise_dontneed, mm_folio_deferred_split, mm_folio_partial_unmap, mmap_fixed_unaligned
```

### 2. 全链路事件统计
```
| 事件 | 次数 | 说明 |
|---|---|---|
| madvise(DONTNEED) | 75,315 | 总释放调用 |
| partial_unmap | 113,361 | 部分解映射 |
| deferred_split(order=2) | 34,634 | 唯一folio延迟拆分 |
```

### 3. 根因分类
```
[饼图]
| 分类 | 次数 | 占比 |
|---|---|---|
| VMA不对齐(mmap是果) | 59,046 | 78.4% |
| madvise不对齐(madv是因) | 14,449 | 19.2% |
| 双方对齐 | 1,820 | 2.4% |
```
配解释文本：说明为什么不对齐会触发 partial split（用 16KB folio 格线图）。

### 4. 每进程 Top 5
```
[堆叠柱状图]
| 进程 | madvise总 | VMA因% | madv因% | →split |
|---|---|---|---|---|
| HeapTaskDaemon | 29,025 | 86.3% | 7.6% | 21,749 |
| ReferenceQueueD | 6,687 | 86.4% | 12.8% | 5,539 |
| 其他 (~200进程) | 39,603 | 66.5% | 27.5% | 12,486 |
```
配解释：HeapTaskDaemon = ART GC，86.3% 的 split 来自 VMA 不对齐。

### 5. A/B 对比（如有补丁/优化）
```
[对比柱状图]
| 指标 | baseline | patched | 变化 |
|---|---|---|---|
| VMA不对齐 | 78.4% | 66.0% | -12.4pp |
| deferred_split | 34,634 | 27,788 | -20% |
```
配解释：补丁生效但 MAP_FIXED 限制全局改善幅度。

### 6. 小结
编号列表，每条结论附数据支撑。

## 图表规范

- 分辨率 ≥ 150 DPI
- 中文字体：`AR PL UMing CN` 或 `SimSun`
- 饼图：标注百分比 + 绝对次数
- 柱状图：堆叠显示分类构成，右侧标注百分比
- 颜色：红色=VMA因，橙色=madvise因，绿色=正常
- 图号连续：图 3-15, 3-16, ...

## 段落文字规范

- 结论先行：每段首句是结论
- 数据紧随：`VMA不对齐从 78.4% 降至 66.0%，降低 12.4 个百分点`
- 不要：`VMA misalignment dropped from 78.4% to 66.0%`
- 归因明确：`归因于 mmap 创建的 VMA 边界不以 16KB 为界`
- 因果链完整：`mmap返回不对齐VMA → madvise覆盖边界folio → partial unmap → deferred split → kswapd拆分`
