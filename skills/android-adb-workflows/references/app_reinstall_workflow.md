# App uninstall/reinstall workflow (adb)

## Goal
Given a **package name** and an **APK path** on the host, do a clean uninstall then reinstall, and smoke-launch.

## Minimal command sequence
```bash
# 1) pick device
adb devices -l

# 2) confirm package exists + where
adb -s <SERIAL> shell pm path <PKG>

# 3) uninstall
adb -s <SERIAL> uninstall <PKG>

# 4) install (single APK)
adb -s <SERIAL> install <APK>

# 5) smoke-launch
adb -s <SERIAL> shell monkey -p <PKG> -c android.intent.category.LAUNCHER 1
```

## Common variants / pitfalls
- If uninstalling a **system app** fails, use: `adb shell pm uninstall --user 0 <PKG>`
- Pipeline note: `aapt` might not exist on the host; don’t rely on it to read package name from an APK.

