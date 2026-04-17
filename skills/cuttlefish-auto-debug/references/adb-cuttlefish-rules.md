# ADB rules for Cuttlefish debug

- Prefer `$RUN_DIR/bin/adb` from `cvd-host_package.tar.gz`.
- Always run `adb root` on userdebug Cuttlefish before tracefs/perfetto setup.
- If multiple devices exist, use `-s <serial>`.
- Use host-side `adb pull/push/install/logcat`; do not run those inside `adb shell`.
- Use device-side `adb shell` for `pm`, `am`, `perfetto`, `mount`, `cat /sys/...`, and workload shell commands.
- Capture `id` and `CapEff` early; uid 0 with `CapEff=0` is not enough for tracefs writes.

Serial selection example:

```bash
ADB=$RUN_DIR/bin/adb
$ADB devices
$ADB -s <serial> root
$ADB -s <serial> shell id
```
