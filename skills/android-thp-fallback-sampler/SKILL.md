---
name: android-thp-fallback-sampler
description: automate long-running sampling of android anon 16KB large folio fallback stats via adb; optionally batch install apks and run monkey workload; outputs raw/derived csv and summary for anon_fallback ratio trending.
---

# Android THP 16KB Anon Fallback Sampler

用来**稳定跑手机端长时间测试**：同时利用
- monkey + adb 压力/切换 workload android-adb-workflows skill
- adb 批量安装 APK wechat-wxapkg-and-apk-batch-tools skill

并在测试期间按固定间隔采样：
`/sys/kernel/mm/transparent_hugepage/hugepages-16KB/stats/*`

重点指标（建议主口径）：

- `fallback_ratio = Δanon_fault_fallback / (Δanon_fault_alloc + Δanon_fault_fallback)`

> 这里把 `anon_fault_fallback` 视作“anon 64K folio 分配失败回退”的次数；
> `anon_fault_alloc` 视作“anon 64K folio 分配成功”的次数。

---

## 什么时候用这个 skill

- 你要对比不同开关组合（anon large folio / mTHP large folio / 其它）在**长时间运行**时 `anon_fallback` 比率是否随时间上升。
- 你有一套可复现 workload（monkey 或脚本），希望把**采样 + 压测 + 安装一堆 app**串成一键流程。

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

### 2) 跑“采样 + monkey”长测

```bash
python3 scripts/run_experiment.py \
  --duration-s 21600 \
  --interval-s 60 \
  --out-dir ./output/thp_run_001 \
  --setup-shell "echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16KB/enabled" \
  --setup-shell "echo always > /sys/kernel/mm/transparent_hugepage/hugepages-16KB/anon" \
  --monkey global
```

如果你要把 monkey 限制在某个 app：

```bash
python3 scripts/run_experiment.py \
  --duration-s 21600 \
  --interval-s 60 \
  --out-dir ./output/thp_run_002 \
  --monkey package \
  --monkey-package com.example.app
```

---

## 产出文件

`--out-dir` 下会生成：

- `raw_samples.csv`：每次采样的原始累计值（单调递增计数器）
- `derived.csv`：每个采样窗口的 Δ 以及 `fallback_ratio`
- `summary.md`：总体比率 + 简单趋势摘要
- `monkey/`：`run_monkey_and_collect_logs.sh` 的产物（logcat、stdout/stderr、dumpsys 等）
- `run_manifest.json`：本次运行的参数、serial、起止时间

---

## 关键参数

### run_experiment.py

- `--serial <SERIAL>`：多设备时必填
- `--duration-s <sec>`：总时长（默认 6h）
- `--interval-s <sec>`：采样间隔（默认 60s）
- `--stats-dir <path>`：stats 目录（默认 16KB stats）
- `--use-su`：用 `su -c` 读 stats / 执行 setup（需要 root）
- `--setup-shell <cmd>`：可重复，运行前执行（建议把开关设置放这）
- `--apk-dir <dir>`：先批量安装该目录下的 `*.apk`
- `--monkey none|global|package`
- `--monkey-package <pkg>`：`package` 模式必填
- `--monkey-throttle-ms <ms>`：默认 75
- `--monkey-events <n>`：不填会按 duration+throttle 估算
- `--monkey-extra "<flags>"`：追加 monkey flags（原样拼接）

---

## 常见坑 / 稳定性建议

- **计数器是累计值**：一定用 `derived.csv` 里的 Δ 计算比率，而不是直接用 raw。
- adb 偶发断开：脚本会对采样做重试，失败会记录 `error` 字段但继续跑。
- setup 命令带重定向：本工具会统一通过 `sh -c` 执行；需要 root 的话配合 `--use-su`。
- 某些设备写 `.../enabled` 这类 sysfs 节点时，`adb shell su -c` 不够，必须带 TTY；脚本里的 THP ensure 写入已按这个方式处理。

---

## Bundled resources

- `scripts/run_experiment.py`：一键跑采样 +（可选）安装 APK +（可选）monkey
- `scripts/derive_metrics.py`：把 raw CSV 变成 derived+summary
- `scripts/run_monkey_and_collect_logs.sh`：来自 android-adb-workflows
- `scripts/apk_batch_install.py`：来自 wechat-wxapkg-and-apk-batch-tools（批量安装逻辑请参阅该 skill 的 SKILL.md）
- `scripts/adb_pkg.sh`, `scripts/adb_helpers.sh`
- `references/adb_execution_reference.md`, `references/monkey_flags.md`
