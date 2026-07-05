# Config templates

本目录保存 `android-thp-fallback-sampler` 的默认运行配置模板（manifest）。

- [`default_memstress_manifest.json`](./default_memstress_manifest.json)：标准 memstress + THP 16KB stats 采样配置。
  - 已固定随机种子、轮次、采样间隔和 memstress 节奏。
  - 已移除 `stats_dir`：脚本会自动探测当前启用的 hugepages stats 目录。
  - 运行前把 `serial` 替换为实际 adb 序列号，或直接在命令行用 `--serial` 覆盖。
  - `packages` 列表是示例；未安装的包会被脚本自动跳过。

模板只包含**可复用的默认参数**。单次真实运行的完整产物（`packages_resolved`、真实时间戳、采样结果）会写在 `--out-dir` 下的 `run_manifest.json`。

使用方式：

```bash
python3 scripts/run_memstress_and_collect_logs.py \
  --serial <YOUR_DEVICE_SERIAL> \
  --from-manifest config/default_memstress_manifest.json
```
