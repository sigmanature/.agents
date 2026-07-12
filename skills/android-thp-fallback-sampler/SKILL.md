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
- `scripts/sample_anon_vma_sizes.py`：从 rooted Android 设备批量采集三方进程 `/proc/<pid>/smaps`，按匿名 VMA kind/process 输出 size/RSS/swap 分位，用来校准 synthetic APK 的 VMA 尺寸和触页强度。
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

当前 COW/order-2 细分也在 `vmstat_raw.csv` / `vmstat_derived.csv` 中采集：
- `cow_mthp_order2`：COW 路径成功复制并安装 order-2 folio。
- `cow_mthp_fallback_order0`：COW 尝试 order-2 后回退并完成 order-0 COW。
- `anon_mthp_vma_unsuitable_order2`：anon fault 中 order-2 被 THP 策略允许、但 VMA suitability/对齐窗口不满足。
- `cow_mthp_vma_unsuitable_order2`：COW 路径中 order-2 被 THP 策略允许、但 VMA suitability/对齐窗口不满足。

## 常见坑

- **设备需要 root**：读取 `/sys/kernel/mm/transparent_hugepage/.../stats` 需要 root。默认用 `su -c`；如果已经 `adb root`，传 `--no-use-su`。
- **stats 目录自动探测**：不需要在 manifest 里写 `stats_dir`，脚本会根据 `/.../enabled` 中 `[always]` 的节点自动选择对应 `stats` 目录。
- **计数器是累计值**：用 `derived.csv` 的 Δ，不要直接对 `raw_samples.csv` 算比率。
- **adb 偶发断开**：采样失败会记录到 `raw_samples.csv` 的 `error` 字段并继续。
- **packages 未安装**：脚本会自动过滤，只启动已安装的包。
- **manifest 里的 seed 固定**：默认 `20260617`；换 seed 会得到不同的包启动顺序，但同一 seed 可复现。

## Synthetic APK Workload

当 Cuttlefish 上真实第三方 x86_64 app 不足、无法稳定制造 ART/Scudo/dlopen/VMA/fork-COW 压力时，先使用 synthetic APK 工作负载，而不是继续扩大不可运行的真实 APK 集合。

入口：

```bash
AOSP_ROOT=$PWD scripts/build_mthp_synth_apks.py \
  --out-dir .worklog/synthetic-mthp-apk/out-$(date +%Y%m%d-%H%M%S)-final \
  --max-pads 64 --pad-rodata-kb 256 --pad-data-kb 64
```

详细构建、安装、验收 profile 和 Android linker/package-manager 坑位见 `references/synthetic_mthp_apk_workload.md`。关键验收 profile：

- 当前 synthetic 语义是简单 full-fault：anonymous VMA 启动后逐页写 fault；pad `.so` 和 filemap 逐页只读 fault；COW 仍由 `cow_pages_per_child` 控制。
- `p00_java_s`：轻量 smoke，期望 `regions=800`、`anon_pages_written=6400`、`dlopen_ok=4`、`mthp_vma=800`。
- `p14_cow_l`：COW smoke，期望 `regions=6000`、`anon_pages_written=24000`、`fork_round=1 children=4 cow_pages_target=65536`。
- `p21_monster_multiproc`：重型多进程，期望主进程 `mthp_vma=6000`，三个 worker 各约 `mthp_vma=2000`，`dlopen_ok=64`。

## Workflow Contract

### Main Workflow
1. 准备设备：确保 adb 连接、已 root、已安装 manifest 中的部分包。
2. 运行：用默认 manifest 执行 `run_memstress_and_collect_logs.py`。
3. 等待运行结束（或按 Ctrl-C 停止）。
4. 验证：检查 `derived.csv` 的 `fallback_ratio` 列和 `summary.md`。
5. 报告：输出 `summary.md`、关键比率趋势、以及 `run_manifest.json`。

### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Package launch resolution | Cuttlefish packages are installed, but `run_memstress_and_collect_logs.py` fails with `RuntimeError: no launchable activities found` | Resolve launcher components through `dumpsys package <pkg>` Activity Resolver Table when `pm resolve-activity` / `cmd package resolve-activity` return no entry | `resolve_activity(serial, pkg)` returns components such as `com.tencent.mm/.ui.LauncherUI` and `resolved_activities.json` is non-empty | Inspect `dumpsys package <pkg>` manually and confirm the package has `MAIN` + `LAUNCHER`; otherwise drop that package from the manifest | branch to dumpsys parser fallback; do not treat this as package-install, kernel, or CVD boot failure |
| Synthetic workload build | CVD真实APK多数因架构/Google服务/ClassNotFound无法启动，压力不足 | Build/install `scripts/build_mthp_synth_apks.py` APK matrix; keep native libs embedded, uncompressed, and `zipalign -P 16` aligned | `p00` shows `dlopen_ok=4`; `p14` shows first fork round; `p21` starts main plus three worker processes | Read `references/synthetic_mthp_apk_workload.md`; do not fall back to extracted native libs because install-time strip can break linker section-header checks | branch to synthetic workload instead of expanding unusable app list |
| Synthetic APK install/collection | install loop advances one APK/process only, or maps collection only reports first process | Ensure every nested `adb` in `while read` loops uses `</dev/null`; install with `adb install --no-incremental -r -g` | Success/fail TSV records all expected APKs; p21 maps count covers main and three workers | Restart only the preload/collection worker; preserve CVD userdata | replace loop implementation |
| Synthetic APK install/collection | Need to preload the 60-APK synthetic matrix onto A/B CVD profiles | Use `scripts/install_mthp_synth_apks_ab.sh run` with `APK_OUT=<synthetic output>` and profile serials `127.0.0.1:16521`/`127.0.0.1:16522` | `install-A/packages.txt` and `install-B/packages.txt` list at least 60 `com.zzhao.mthp.synth` packages | If adb is offline, launch/fix the profile first through the A/B CVD workflow; do not let adb consume profile TSV input | continue to smoke or sampler |
| Synthetic long run | Running synthetic packages through `run_memstress_and_collect_logs.py` | Preserve the skill/default-manifest pressure knobs explicitly: `--burst-size 4 --hold-ms 15 --launch-gap-ms 15 --cycle-sleep-ms 1000 --seed 20260617`; passing only `--package-file` falls back to the script's lower-pressure built-in defaults | `run_manifest.json` records burst `4`, hold `15`, launch gap `15`, and seed `20260617` | If these values are absent or differ unintentionally, discard the cell and rerun with explicit knobs or `--from-manifest` | replace command |
| Synthetic long run | CVD relaunch or image rotation leaves fewer than 60 synthetic packages installed, even after a previous 60/0 preload | After every active-profile relaunch, auto-run `scripts/install_mthp_synth_apks_ab.sh run` with `APK_OUT=<synthetic output>` if `WORKLOAD_PACKAGE_FILE` packages are missing, then wait on `pm path` package evidence | Package check against `WORKLOAD_PACKAGE_FILE` reaches 60 before sampler starts; early `cycle_log.jsonl` has zero launch errors; logcat has `ZZMthpSynthNative`, `regions=`, and COW `fork_round=` markers | If `APK_OUT` is unavailable or install fails, abort the cell early; do not wait forever or continue a 120-cycle run with stale package-count evidence | branch to post-relaunch installer |
| Synthetic workload calibration | Long run shows near-zero `allocstall`/direct reclaim or synthetic pressure is suspected too weak | Sample real device anonymous VMA size/RSS with `scripts/sample_anon_vma_sizes.py`, then compare against synthetic `vma_size_kb`, COW pages, and filemap size | `anon_kind_summary.tsv` and `anon_process_summary.tsv` separate virtual reservations from RSS-bearing VMAs | If only idle/system apps are sampled, launch representative heavy apps and resample before retuning | branch to workload retuning before rerunning A/B |
| Synthetic workload semantics | Need deterministic resident pressure rather than sparse/partial touches | Keep anonymous synthetic VMAs fully write-faulted at startup, keep pad `.so` and filemap paths full read-faulted, and record `anon_full_fault_pages` / `anon_fault_mode` in `profiles.tsv` | logcat shows `anon_pages_written=...`; `profiles.tsv` has `anon_fault_mode=full_write`, `so_fault_mode=full_read`, and `filemap_fault_mode=full_read` | If full-write OOMs immediately, retune `vma_count`/`vma_size_kb` in builder while preserving full-fault semantics; do not reintroduce sparse `parent_touch_pages` as the main pressure knob | replace sparse-touch workload model |

### Output Contract
- 运行脚本：`scripts/run_memstress_and_collect_logs.py`
- 使用 manifest：`config/default_memstress_manifest.json`
- 输出目录：`--out-dir` 指定，或默认 `/tmp/thp_memstress_<timestamp>`
- 关键产物：`derived.csv`（含 `fallback_ratio`）、`summary.md`（含 `anon_alloc`/`anon_fallback`/`fallback_ratio`/`alloc_stall`/`compact_stall`，均为 end - start）、`run_manifest.json`

## Precondition (可选独立脚本)

按实验需要在 memstress 之前运行，用来制造碎片化初始状态。默认 A/B 短测和常规长测不强制运行 precondition；只有明确要测碎片化初始状态或验证 fallback 压力时才启用。启用时，脚本必须在 THP never 下执行（脚本自动设置）。

```bash
python3 scripts/precondition.py --serial <SERIAL> --alloc-mb 5000 --threshold 2000
```

- 自动重启设备、等待 su 就绪
- 强制 THP never → 运行 fragmem（全 order-0 分配 + munmap 碎片化）
- fragmem 在后台 hold 内存，实验结束后 `killall fragmem`

可选流程顺序：
1. 不启用 precondition：直接设置 THP / sysctl 配置，然后运行 `run_memstress_and_collect_logs.py`。
2. 启用 precondition：先运行 `precondition.py`（重启 + 碎片化，THP never）。
3. 启用 precondition 后：不再重启，重新设置 THP / sysctl 配置，再运行 `run_memstress_and_collect_logs.py --post-prepare-cmd '...'`（温控 → 锁频 → post-prepare 设 compaction → workload）。

**规则**：precondition 是可选变量；启用后不再重启，碎片状态通过 fragmem hold 保持。

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
