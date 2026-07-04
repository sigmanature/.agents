---
name: android-thp-fallback-sampler
description: automate long-running sampling of android anon 16KB large folio fallback stats via adb; run memstress workload and output raw/derived csv + summary.
---

# Android THP 16KB Anon Fallback Sampler

> **最短复现入口**: 仓库根目录 [`README.md`](../README.md)
> **默认配置模板**: [`config/default_memstress_manifest.json`](../config/default_memstress_manifest.json)

本 skill 只保留一个核心脚本：
- `scripts/run_memstress_and_collect_logs.py`：在已 root 的 Android 设备上运行 memstress，并周期性采样 THP 16KB/32KB/64KB stats，输出 `raw_samples.csv` / `derived.csv` / `summary.md`。

## 什么时候用

- 需要长时间运行一个可控的 app 启停负载，并同时采样 anon large folio 的 fallback 比率。
- 希望复现实验：同样的 manifest + seed 可以跑出相同的包启动顺序。

## 快速开始

见 [`README.md`](../README.md)。最短命令：

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json
```

## 文件说明

- `config/default_memstress_manifest.json`：默认 memstress + THP stats 采样配置模板，已固定 seed / max_cycles / interval_s。
- `scripts/run_memstress_and_collect_logs.py`：主脚本。
- `scripts/derive_metrics.py`：运行结束后由主脚本调用，生成 `derived.csv` 和 `summary.md`。
- `scripts/utils/`：主脚本依赖的公共模块（adb/su、设备准备、采样、包解析、崩溃检测等）。
- `references/`：与 adb、memstress 策略、包选择、内核补丁相关的参考文档。

## 核心指标

重点看 `derived.csv` 里的：

```
fallback_ratio = Δanon_fault_fallback / (Δanon_fault_alloc + Δanon_fault_fallback)
```

含义：
- `anon_fault_alloc`：anon 64K folio 分配成功次数。
- `anon_fault_fallback`：anon 64K folio 分配失败回退次数。

计数器是累计单调值，比率必须用相邻采样窗口的 Δ 计算。

## 常见坑

- **设备需要 root**：读取 `/sys/kernel/mm/transparent_hugepage/.../stats` 需要 root。默认用 `su -c`；如果已经 `adb root`，传 `--no-use-su`。
- **stats 目录自动探测**：不需要在 manifest 里写 `stats_dir`，脚本会根据 `/.../enabled` 中 `[always]` 的节点自动选择对应 `stats` 目录。
- **计数器是累计值**：用 `derived.csv` 的 Δ，不要直接对 `raw_samples.csv` 算比率。
- **adb 偶发断开**：采样失败会记录到 `raw_samples.csv` 的 `error` 字段并继续。
- **packages 未安装**：脚本会自动过滤，只启动已安装的包。
- **manifest 里的 seed 固定**：默认 `20260617`；换 seed 会得到不同的包启动顺序，但同一 seed 可复现。

## Workflow Contract

### Main Workflow
1. 准备设备：确保 adb 连接、已 root、已安装 manifest 中的部分包。
2. 运行：用默认 manifest 执行 `run_memstress_and_collect_logs.py`。
3. 等待运行结束（或按 Ctrl-C 停止）。
4. 验证：检查 `derived.csv` 的 `fallback_ratio` 列和 `summary.md`。
5. 报告：输出 `summary.md`、关键比率趋势、以及 `run_manifest.json`。

### Output Contract
- 运行脚本：`scripts/run_memstress_and_collect_logs.py`
- 使用 manifest：`config/default_memstress_manifest.json`
- 输出目录：`--out-dir` 指定，或默认 `/tmp/thp_memstress_<timestamp>`
- 关键产物：`derived.csv`（含 `fallback_ratio`）、`summary.md`（含 `anon_alloc`/`anon_fallback`/`fallback_ratio`/`alloc_stall`/`compact_stall`，均为 end - start）、`run_manifest.json`

## Precondition (独立脚本)

在 memstress 之前运行，制造碎片化初始状态。**必须在 THP never 下执行**（脚本自动设置）。

```bash
python3 scripts/precondition.py --serial <SERIAL> --alloc-mb 5000 --threshold 2000
```

- 自动重启设备、等待 su 就绪
- 强制 THP never → 运行 fragmem（全 order-0 分配 + munmap 碎片化）
- fragmem 在后台 hold 内存，实验结束后 `killall fragmem`

流程顺序：
1. `precondition.py`（重启 + 碎片化，THP never）
2. 设 THP / sysctl 配置（此时不设 compaction 开关）
3. `run_memstress_and_collect_logs.py --post-prepare-cmd '...'`（温控 → 锁频 → post-prepare 设 compaction → workload）

**规则**：precondition 后不再重启，碎片状态通过 fragmem hold 保持。

## CPU Accounting (独立脚本)

采集 kcompactd/kswapd CPU 时间 + direct reclaim/compact 精确耗时。与主脚本解耦。

```bash
# 在 memstress 前启动 trace:
python3 scripts/trace_cpu_accounting.py start --serial <SERIAL> --out-dir <RUN_DIR>

# (跑 memstress)

# memstress 结束后收集:
python3 scripts/trace_cpu_accounting.py stop --serial <SERIAL> --out-dir <RUN_DIR>

# 离线分析:
python3 scripts/trace_cpu_accounting.py analyze --out-dir <RUN_DIR>
```

产出：
- `schedstat_start.json` / `schedstat_end.json`：kcompactd/kswapd 的 on_cpu_ns, wait_ns, timeslices
- `ftrace_mm.txt`：raw ftrace（mm_vmscan_direct_reclaim_begin/end, mm_compaction_begin/end）
- `direct_reclaim_stats.json`：解析后的 direct reclaim/compact 总耗时和次数

**规则**：
- trace 脚本不影响主脚本的任何行为
- schedstat 零开销（读 /proc）
- ftrace mm instance 独立 buffer，事件量小（几万级），开销可忽略
- 随机种子永远不动：`20260617`
