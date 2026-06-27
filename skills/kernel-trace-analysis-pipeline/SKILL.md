# Kernel Trace Analysis Pipeline

内核 trace 分析全流程工作流，从加 tracepoint 到生成 Word 报告。

## Pipeline Overview

```
1. 加tracepoint  →  2. 编译刷机  →  3. 实验采集  →  4. trace分析  →  5. 生成报告
   ↓                  ↓               ↓               ↓               ↓
 kernel-            build_          long_shell      log-fast-       bishe-guider
 tracepoint-        slider.sh       _spec.md        search          报告规范
 pattern
```

## 各阶段规范

### 阶段 1：加 tracepoint

**Reference:** `~/.agents/skills/kernel-tracepoint-pattern/SKILL.md`

核心规则：
- `include/trace/events/<subsystem>.h` — TRACE_EVENT 定义
- 找到该 subsystem 的 `CREATE_TRACE_POINTS` 所在 .c 文件（只有一个）
- 调用方只要 `#include <trace/events/<subsystem>.h>`（不带 CREATE_TRACE_POINTS）
- 加完用 grep 验证：同一个 TRACE_EVENT 只出现在一个 header 里

常见坑：
- 重复符号 → 两个 .c 都定义了 CREATE_TRACE_POINTS
- 未声明函数 → 调用方没 include trace header
- bazel 缓存旧 .o → 清 `out/bazel/.../execroot/_main/bazel-out` 重编

### 阶段 2：编译刷机

```bash
cd ~/learn_os/pixel
bash build_slider.sh              # 编译
cp out/slider/dist/boot.img out/slider/dist/boot_xxx.img  # 备份

# 刷机
adb reboot bootloader; fastboot flash boot out/slider/dist/boot.img; fastboot reboot
```

**Reference:** `~/learn_os/pixel/build_slider.sh` 自带 `--strategy=local` 绕开 bazel sandbox 问题。

### 阶段 3：实验采集

**Reference:** `~/.agents/references/long_shell_spec.md`

```bash
S=<serial>; T=/sys/kernel/tracing

# 1. THP 配置 (按需)
echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16kB/enabled
echo always > /sys/kernel/mm/transparent_hugepage/defrag

# 2. ftrace 事件
for evt in <event1> <event2>; do
  echo 1 > $T/events/<subsys>/$evt/enable
  echo 1 > $T/events/<subsys>/$evt/stacktrace  # 要调用栈就开
done
echo mono > $T/trace_clock
echo 32768 > $T/buffer_size_kb

# 3. trace 写设备 tmpfs (防 OOM, 零闪存损耗)
nohup cat $T/trace_pipe > /tmp/trace.txt &
echo -1000 > /proc/$!/oom_score_adj

# 4. 跑 workload (memstress 等)
setsid python3 run_memstress.py ... >> log 2>&1 &

# 5. 跑完拉 trace
adb pull /tmp/trace.txt ./trace/trace_chunk_1.txt
```

关键规则：
- trace 写 `/tmp`（tmpfs，纯 RAM，不损耗 UFS）
- `oom_score_adj = -1000` 防止被 LMK 杀
- `setsid` 脱离终端，`flock` 防多实例
- PID + 日志路径全部记入 `pids.tsv`
- 跑完提供 `cleanup.sh` 一键终止

### 阶段 4：trace 分析

**Reference:** `~/.agents/skills/log-fast-search/SKILL.md`
**归因规范:** `references/classification_pattern.md`

#### 4a. rg 预过滤 + 流式解析

```python
# 核心模式: rg 提取 → Python 流式解析 → bisect 关联
raw = subprocess.run(['rg', '--no-heading', '-N', '-j', '0',
                      'event_name'], capture_output=True, text=True).stdout
events = [parse(line) for line in raw.splitlines()]
```

**禁止做法：**
- `f.read()` 全加载 300MB 文件 → OOM
- 嵌套 `for e1 in events: for e2 in events:` → O(n²)
- 用 `grep` 替代 `rg` → 慢 10x

#### 4b. 归因分类 + 因果链追溯

分类必须：
- 互斥且完备（所有事件归入恰好一类）
- 判定条件可追溯（用 trace 具体字段）
- 附带 **因果链代码行**，格式：`mm/madvise.c:872 → zap_page_range_single_batched()`

**禁止只写文字原因不写代码行。** 参考 `references/classification_pattern.md#因果链追溯`

#### 4c. 多方指标交叉校验

分析后必须校验：
- 分类之和 = 总事件数（完备性）
- per_process 之和 = 总数（聚合正确）
- partial / split 比率合理（3-10x）
- 触发 split 次数 ≤ 总 madvise 数

参考 `references/classification_pattern.md#多方指标交叉校验`

#### 4c. bisect 时间窗口关联

```python
from bisect import bisect_left

def has_event_nearby(sorted_ts_list, target_ts, window_us=10000):
    lo = bisect_left(sorted_ts_list, target_ts)
    return lo < len(sorted_ts_list) and (sorted_ts_list[lo] - target_ts) * 1e6 <= window_us
```

用于回答："事件 A 发生后 10ms 内是否触发了事件 B？"

#### 4d. 输出 JSON 规范

```json
{
  "events": { "总事件A": N, "总事件B": M },
  "classification": { "A类": n1, "B类": n2, "正常": n3 },
  "correlation": { "A→B(关联)": k },
  "per_process": { "进程名": { "总次数": t, "A类": a, "B类": b, "→B": c } },
  "runtime_s": 6.7
}
```

- `per_process` 按总次数降序
- 每个指标用中文键名，别用英文缩写

### 阶段 5：生成报告

#### 5a. 图表

```python
# 饼图 — 根因分类总览
# 堆叠柱状图 — 每进程对比
# 频率直方图 — 偏移分布
# 对比柱状图 — A/B 效果
```

字体：用 `AR PL UMing CN` 或系统自带中文字体。
Reference chart generator: `~/learn_os/runs/.../generate_charts.py`

#### 5b. 报告规范

**指标解释规则：**
- 中文全称 + 括号内标注 trace 事件名，如：延迟拆分次数（mm_folio_deferred_split）
- 百分比标注分子分母，如：VMA不对齐占比 = 59046/75315 = 78.4%
- 对比数据用表格，表头带基线/实验/变化三列

**段落结构：**
1. 实验配置（device, workload, 轮数, THP 模式）
2. 总体统计数据（表格）
3. 分类分析（饼图 + 解释）
4. 每进程拆解（Top 5 + 其他，堆叠柱状图）
5. A/B 对比结论（如有）
6. 小结（编号列表）

**禁止做法：**
- 只用英文变量名不解释
- 只说百分比不说绝对数
- 图表无中文标题/轴标签

#### 5c. 写入 Word

使用 python-docx（conda base 环境）：
```python
from docx import Document
doc = Document('template.docx')
# 找到插入点，用 addprevious() 插入新段落/图表/表格
```

Reference inserter: `~/learn_os/runs/.../insert_final.py`

## Workflow Contract

### Main Workflow
1. 确定分析目标 → 设计 tracepoint 需求
2. 加 tracepoint (kernel-tracepoint-pattern) → 编译刷机
3. 设计实验配置 → 写采集脚本 (long_shell_spec)
4. 运行实验 → 拉 trace
5. rg 预过滤 → 流式解析 → 归因分类 → bisect 关联 → JSON 输出 (log-fast-search)
6. 生成图表 (matplotlib) → 写 Word 报告 (python-docx)

### Decision Table
| Phase | Trigger | Action | Reference |
|---|---|---|---|
| tracepoint 编译 | duplicate symbol | 找到该 subsystem 唯一 CREATE_TRACE_POINTS 文件 | kernel-tracepoint-pattern |
| tracepoint 编译 | bazel cache 复用旧 .o | `rm -rf out/bazel/.../bazel-out` 强制重编 | build_slider.sh |
| 采集 | trace 丢失 | 检查 oom_score_adj=-1000，trace 写 /tmp 不是 /data | long_shell_spec |
| 分析 | 300MB trace OOM | 用 rg 预过滤，不 `f.read()` | log-fast-search |
| 分析 | 分析超时 | 用 bisect 替代嵌套循环 | log-fast-search |
| 报告 | 图表无中文 | 设 `plt.rcParams['font.family'] = 'AR PL UMing CN'` | generate_charts.py |

### Output Contract
- 每个 trace 文件对应一份 `analysis_result.json`
- 每轮实验对应一份 `pids.tsv` + `cleanup.sh`
- 最终报告含图表 PNG + Word docx

## 脚本资产

| 脚本 | 路径 | 用途 |
|---|---|---|
| analyze_fast.py | `~/learn_os/runs/.../analyze_fast.py` | rg+解析+关联+JSON 输出 |
| analyze_v2.py | `~/learn_os/runs/.../analyze_v2.py` | v2 含 MAP_FIXED 统计 |
| generate_charts.py | `~/learn_os/runs/.../generate_charts.py` | matplotlib 图表生成 |
| insert_final.py | `~/learn_os/runs/.../insert_final.py` | python-docx 插入报告 |
| run_memstress_slim.py | `~/.agents/skills/android-thp-fallback-sampler/scripts/` | 瘦身版 memstress |

## 交叉引用

- `kernel-tracepoint-pattern` — 加 tracepoint 规范
- `log-fast-search` — rg + bisect 大日志分析
- `bishe-guider` — 论文章节结构和写作规范
- `long_shell_spec.md` — 长期脚本运行规范（setsid, flock, cleanup）
- `mmap-lock-contention-analysis` — mmap_lock 竞争分析（含 trace 采集脚本）
- `android-thp-fallback-sampler` — memstress workload 工具