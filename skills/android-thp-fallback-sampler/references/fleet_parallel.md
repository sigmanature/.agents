# 多设备并行（单进程）约定

本 skill 的实验脚本（例如 `scripts/run_monkey.py`、`scripts/run_memstress_and_collect_logs.py`）支持：

- `--serial SERIAL_A --serial SERIAL_B`（可重复）
- `--serial SERIAL_A,SERIAL_B`（逗号分隔）
- `--all-devices`（跑所有 `adb devices` 中状态为 `device` 的设备）
- `--jobs N` 控制并行度（线程池）

## 输出目录分层

- 单设备：产物直接落在 `--out-dir`（或默认 `output/...`）目录下
- 多设备：按 `--out-dir/<serial>/...` 分层隔离

这样做的目的：

- 同一个实验在多台设备上跑时，输出天然隔离，不会互相覆盖
- 后续聚合/对比可以按 serial 维度采集

## 并发实现说明

实现方式是 host 侧线程池并发（I/O bound 的 adb 调用），每个设备一个 worker：

- 每个 worker 使用 `adb -s <serial> ...`，不会串台
- Ctrl-C / SIGINT 会通过**全局 stop_event** 请求各 worker 尽快收尾（停止采样/退出循环/结束 workload）

## 常见稳定性点（fleet 模式）

- **每设备独立收尾**：每个设备内部会用一个“本地 stop”来停止该设备的采样线程并完成 `derive_metrics`，不会因为某个设备正常结束而把其他设备提前停掉。
- **adb shell timeout 不再直接打断整轮**：例如 `am start -W ...` 可能偶发卡住。脚本会把 `adb shell` 超时视为一次失败（`returncode=124`），记录到 `memstress/cycle_log.jsonl` 的 `launch_errors`，然后继续跑下一包/下一轮。
