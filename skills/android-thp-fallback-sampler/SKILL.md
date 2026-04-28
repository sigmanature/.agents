---
name: android-thp-fallback-sampler
description: automate long-running sampling of android anon 16KB large folio fallback stats via adb; optionally batch install apks and run monkey workload; outputs raw/derived csv and summary for anon_fallback ratio trending.
---

# Android THP 16KB Anon Fallback Sampler

用来**稳定跑手机端长时间测试**：同时利用
- monkey + adb 压力/切换 workload android-adb-workflows skill
- memstress 启停循环 workload（快速启动多 app、保活一批、再 force-stop 一批）
- adb 批量安装 APK wechat-wxapkg-and-apk-batch-tools skill

并在测试期间按固定间隔采样：
`/sys/kernel/mm/transparent_hugepage/hugepages-16kB/stats/*`

重点指标（建议主口径）：

- `fallback_ratio = Δanon_fault_fallback / (Δanon_fault_alloc + Δanon_fault_fallback)`

> 这里把 `anon_fault_fallback` 视作“anon 64K folio 分配失败回退”的次数；
> `anon_fault_alloc` 视作“anon 64K folio 分配成功”的次数。

---

## 什么时候用这个 skill

- 你要对比不同开关组合（anon large folio / mTHP large folio / 其它）在**长时间运行**时 `anon_fallback` 比率是否随时间上升。
- 你有一套可复现 workload（monkey、重内存 app 启停循环，或脚本），希望把**采样 + 压测 + 安装一堆 app**串成一键流程。

---

## 快速开始

> 运行都在**你的电脑(Host)** 上执行，手机通过 adb 连接。

### 0) 前置检查

```bash
adb devices
# 确认设备是 device 状态
```

### 1) 可选：批量安装 APK

> 批量安装能力由 `wechat-wxapkg-and-apk-batch-tools` skill 统一维护，本 skill 仅复用该能力。

```bash
python3 scripts/apk_batch_install.py ./apks --output-dir ./output/apk_install_run_001
```

如果 `./apks` 是你用符号链接拼出来的 staging 目录，脚本现在会在存在断链 `*.apk` 时直接报错并点名缺失项，先修好 staging 再继续长测。

### 2) 跑“采样 + monkey”长测

```bash
python3 scripts/run_monkey.py \
  --serial <SERIAL> \
  --duration-s 21600 \
  --interval-s 60 \
  --out-dir ./output/thp_run_001 \
  --thp-ensure-mode always \
  --setup-shell "echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16kB/anon" \
  --monkey global
```

如果你要把 monkey 限制在某个 app：

```bash
python3 scripts/run_monkey.py \
  --serial <SERIAL> \
  --duration-s 21600 \
  --interval-s 60 \
  --out-dir ./output/thp_run_002 \
  --monkey package \
  --monkey-package com.example.app
```

### 3) 跑“采样 + memstress”长测

适用于更强调**快速切换/冷启动 churn** 的场景：每轮快速启动多 app，每次启动后 hold 一小段时间，然后按 HOME 返回桌面（不 force-stop，不做 LRU）。

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <SERIAL> \
  --duration-s 21600 \
  --interval-s 60 \
  --out-dir ./output/thp_memstress_001 \
  --thp-ensure-mode always \
  --setup-shell "echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16kB/anon" \
  --package-file ./top100_packages.txt \
  --heavy-package com.google.android.GoogleCamera \
  --heavy-package com.google.android.apps.youtube.unplugged \
  --burst-size 4 \
  --heavy-per-burst 2 \
  --hold-ms 200
```

---

## 产出文件

`--out-dir` 下会生成：

- `raw_samples.csv`：每次采样的原始累计值（单调递增计数器）
- `derived.csv`：每个采样窗口的 Δ 以及 `fallback_ratio`
- `summary.md`：总体比率 + 简单趋势摘要
- `monkey/`：`run_monkey.py` 的产物（logcat、stdout/stderr、dumpsys 等）
- `memstress/`：`run_memstress_and_collect_logs.py` 的产物（轮次日志、resolved activities、logcat、summary）
- `run_manifest.json`：本次运行的参数、serial、起止时间

---

## 关键参数

> 每个 `scripts/run_*.py` 顶部都有一个 `CONFIG` 常量区：默认参数以 `CONFIG` 为准；命令行参数用于按需覆盖。

### run_monkey.py

- `--serial <SERIAL>`：多设备时必填
- `--duration-s <sec>`：总时长（默认 6h）
- `--interval-s <sec>`：采样间隔（默认 60s）
- `--stats-dir <path>`：stats 目录（默认 16KB stats）
- `--use-su/--no-use-su`：是否用 `su -c` 读 stats / 执行 setup（需要 root）
- `--setup-shell <cmd>`：可重复，运行前执行（建议把开关设置放这）
- `--apk-dir <dir>`：先批量安装该目录下的 `*.apk`
- `--thp-ensure-mode <mode>`：通过 `<stats_dir_parent>/enabled` 确保模式（如 `always`；用 `none` 表示只检查不写）
- `--no-thp-ensure`：跳过 enabled 检查/写入
- `--monkey global|package`
- `--monkey-package <pkg>`：`package` 模式必填
- `--monkey-throttle-ms <ms>`：默认 75
- `--monkey-events <n>`：不填会按 duration+throttle 估算
- `--monkey-extra "<flags>"`：追加 monkey flags（原样拼接）

### run_memstress_and_collect_logs.py

- `--serial <SERIAL>`：多设备时必填
- `--duration-s <sec>`：总时长（默认 6h）
- `--interval-s <sec>`：采样间隔（默认 60s）
- `--stats-dir <path>` / `--counters <csv>`：采样源与 counter 列表
- `--use-su/--no-use-su`：是否用 `su -c` 读 stats / 执行 setup（需要 root）
- `--setup-shell <cmd>`：可重复，运行前执行（建议把开关设置放这）
- `--thp-ensure-mode <mode>` / `--no-thp-ensure`：同上
- `--package <pkg>` / `--package-file <file>`：memstress 的目标 app 集合
- `--heavy-package <pkg>` / `--heavy-package-file <file>`：显式标记重型 app，优先在每轮启动
- `--burst-size <n>` / `--heavy-per-burst <n>`：每轮启动数量与 heavy 目标数
- `--hold-ms <ms>`：每次成功启动后 hold（用于“闪进一会再回桌面”的节奏控制；默认 200ms）
- `--launch-gap-ms <ms>` / `--cycle-sleep-ms <ms>`：启动间隔与轮间隔
- `--prefer-keywords <csv>`：关键词自动偏置（camera/video/...）
- `--oat-prune-watch`：运行期间轮询目标包并删除重新生成的 `oat/odex/vdex/art`
- `--oat-prune-package <pkg>` / `--oat-prune-package-file <file>`：覆盖默认 watcher 包集；默认用当前 memstress 已安装目标包
- `--oat-prune-poll-s <sec>`：watcher 轮询周期；建议从 `2s` 起步

---

## 多设备并行（单进程）

> 两个实验脚本都支持：一个进程内对多个设备并行跑（线程池并发）。

常用两种方式：

1) 手动指定多个 serial（可重复或逗号分隔）：

```bash
python3 scripts/run_monkey.py \
  --serial SERIAL_A --serial SERIAL_B \
  --jobs 2 \
  --out-dir ./output/thp_monkey_fleet_001 \
  --duration-s 21600 --interval-s 60 \
  --monkey global
```

2) 自动跑所有在线设备：

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --all-devices \
  --jobs 4 \
  --out-dir ./output/thp_memstress_fleet_001 \
  --duration-s 21600 --interval-s 60 \
  --package-file ./top100_packages.txt
```

输出目录约定：
- 单设备：产物直接落在 `--out-dir`（或默认 `output/...`）目录下
- 多设备：按 `--out-dir/<serial>/...` 分层隔离

---

## 常见坑 / 稳定性建议

- **计数器是累计值**：一定用 `derived.csv` 里的 Δ 计算比率，而不是直接用 raw。
- adb 偶发断开：脚本会对采样做重试，失败会记录 `error` 字段但继续跑。
- adb 显示 `device offline`：参考 `references/adb_device_offline_recovery.md` 的恢复步骤（`adb reconnect offline` / 重启 adb server）。
- setup 命令带重定向：本工具会统一通过 `sh -c` 执行；需要 root 的话配合 `--use-su`。
- 某些设备写 `.../enabled` 这类 sysfs 节点时，`adb shell su -c` 不够，必须带 TTY；脚本里的 THP ensure 写入已按这个方式处理。
- 同类的 per-filesystem sysfs 节点也可能有这个限制，例如 Pixel 6 上的 `/sys/fs/f2fs/dm-49/max_folio_order_cap` 和 `/sys/fs/ext4/dm-3/max_folio_order_cap`；遇到 `Permission denied` 时优先走 `tty=True` 的 root 执行路径。
- 某些设备上，monkey 前的亮屏/解锁必须用朴素的 `input keyevent KEYCODE_WAKEUP`、`wm dismiss-keyguard`、`input swipe`；`cmd input keyboard ...` 这类写法可能不会真正把设备从 `Dozing` 拉到 `Awake`。
- monkey runner 现在默认带 `--ignore-native-crashes`，避免某个 app 的 native crash 直接把整轮 workload 打断；只有显式传 `--abort-on-native-crash` 时才恢复 crash-stop 行为。
- memstress 只会在你显式传入的 package 集合内循环，不会像 `monkey --global` 那样全域乱跑；如果想强行偏向相机/视频，优先传明确的 `--memstress-heavy-package`，不要只依赖关键词猜测。
- memstress 当前策略已精简为：`am start`（不带 `-W`）+ hold + HOME，不做 `force-stop`/LRU；详见 `references/memstress_strategy.md`。
- memstress 的 classloading crash 监测现在只会对**目标 workload 包**生效；无关系统包的 `am_crash + ClassNotFoundException` 不会再把整轮实验误停。对应回归测试见 `tests/test_crash_signature.py`。
- 如果后台 `dex2oat/artd` 会持续把目标包的编译产物补回来，可以开启 `--oat-prune-watch`；watcher 同时覆盖包目录下的 `oat/` 和 `/data/dalvik-cache`，但会明确跳过 `*.tmp`，避免碰发布中的临时文件。对应脚本见 `scripts/watch_oat_prune.py` 和 `tests/test_oat_watch.py`。
- `watch_live_plot.py` 默认会在 `--out-dir` 下寻找 `<serial>/raw_samples.csv`，更适合 fleet/多设备目录；如果是单设备 direct-out-dir（`raw_samples.csv` 直接落在运行目录根），可把“运行目录的父目录”传给 `--out-dir`，把“运行目录 basename”当作 `--serial`，或者直接手工跑一次 `derive_metrics.py` 做即时对比。

---

## Bundled resources

- `scripts/run_monkey.py`：跑采样 + monkey（logcat + monkey stdout/stderr + dumpsys）
- `scripts/run_memstress_and_collect_logs.py`：跑采样 + memstress（logcat + cycle log + dumpsys）
- `scripts/launch_memstress_detached.sh`：可靠后台启动 memstress（setsid + pidfile + stdout/stderr）
- `scripts/launch_memstress_uc_douyin_huoshan_detached.sh`：三 app 循环一键后台启动（UC + 抖音 + 火山）
- `scripts/watch_oat_prune.py`：独立 sidecar watcher，轮询目标包并删除 regenerated `oat/odex/vdex/art`（跳过 `*.tmp`）
- `scripts/plot_derived_svg.py`：把 `derived.csv` 画成 `SVG`（无 matplotlib/pandas 依赖；支持多设备多曲线）
- `scripts/watch_live_plot.py`：长测期间定期从 `raw_samples.csv` 生成临时 `derived.csv` 并更新对比 `SVG`（`latest/` + `archive/`，支持 `fallback_ratio` / `cumulative_fallback` / `cumulative_ratio`）

### 绘图示例（无 matplotlib）

单设备：

```bash
python3 scripts/plot_derived_svg.py ./output/thp_memstress_run_001/derived.csv --out-dir ./output/plot_run_001
```

双设备对比（同一 out_dir 下的两个 serial 子目录）：

```bash
python3 scripts/plot_derived_svg.py \
  ./output/thp_memstress_fleet_001/<SERIAL_A>/derived.csv \
  ./output/thp_memstress_fleet_001/<SERIAL_B>/derived.csv \
  --align absolute \
  --out-dir ./output/plot_fleet_001
```

单设备 detached run 的 live plot：

```bash
RUN=./output/memstress_20260420_220754_1A071FDF600053
python3 scripts/watch_live_plot.py \
  --out-dir "$(dirname "$RUN")" \
  --serial "$(basename "$RUN")" \
  --plot-dir "$RUN/live_plot" \
  --every-s 30 \
  --align absolute \
  --metric cumulative_fallback
```
- `scripts/run_experiment.py`：兼容 wrapper（deprecated）
- `scripts/derive_metrics.py`：把 raw CSV 变成 derived+summary
- `scripts/compare_derived.py`：对比两个 `derived.csv`（有 matplotlib 时输出对比图）
- `scripts/apk_batch_install.py`：来自 wechat-wxapkg-and-apk-batch-tools（批量安装逻辑请参阅该 skill 的 SKILL.md）
- `scripts/run_thp_memstress_top100_dual_9h.sh`：双设备一键编排（可选批量安装 top100 APK + 9h memstress + 画图）
- `scripts/adb_pkg.sh`, `scripts/adb_helpers.sh`
- `scripts/utils/`：公共函数（adb/tty/su、设备亮屏解锁常亮、采样、THP ensure、out-dir/setup/install 工具函数）
- `references/adb_execution_reference.md`, `references/monkey_flags.md`
- `references/apk_batch_install_flatten_dir.md`：当 APK 分散在多个目录时的“扁平化”安装目录做法 + 常见 install 失败排查
- `references/long_run_detach.md`：在 Codex/非交互环境里可靠地后台启动多小时任务（setsid + pidfile）
- `references/app_subset_selection.md`：为 flash-kill/短周期 churn 挑选 ≤20 个“重型 app”子集的建议与校验规则
- `references/memstress_strategy.md`：memstress 简化策略（`am start` + hold + HOME，不 force-stop）
- `references/memstress_package_validation.md`：解释 memstress 为什么要校验/解析包名
- `references/fleet_parallel.md`：解释单进程多设备并行与输出目录分层
