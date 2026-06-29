# Config templates

这个目录保存 `android-thp-fallback-sampler` 的默认运行配置模板（manifest）。

> **Agent 最短理解路径**：先看 [`default_memstress_manifest.json`](./default_memstress_manifest.json)，它描述了一次标准 memstress 采样任务的全部默认参数。

## 文件说明

- `default_memstress_manifest.json`
  - 默认的 memstress + THP 16KB stats 采样配置。
  - 包含：目标包列表、采样 counters、采样间隔、`memstress` 启停参数。
  - 运行前需要把 `serial` 替换为实际 adb 序列号，`start_host_ts` / `end_host_ts` / `status` / `samples` / `sample_errors` 由运行脚本自动填充。

## 如何扩展

如果要新增一种 workload 的默认配置（例如 `monkey_global_manifest.json`、`refault_probe_manifest.json`、`fleet_4devices_manifest.json`），直接在本目录下新增 JSON 文件，并在 `SKILL.md` 的"最短理解路径"里按名字引用。

## 与真实运行的关系

- 本目录只放**模板**（可复用的默认参数）。
- 单次实际运行的完整产物（含 `packages_resolved`、真实时间戳、采样结果）会写在 `--out-dir` 下的 `run_manifest.json`。
- 默认模板来源：`/home/nzzhao/runs/thp_memstress_120cycles_20260625/run_manifest.json`。
