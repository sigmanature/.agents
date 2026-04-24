# Android “ClassNotFoundException/NoClassDefFoundError” widespread crash triage (storage-aware)

This note targets a **pattern**:

- Many apps (including system apps like Launcher/Settings) crash in a loop with `ClassNotFoundException` / `NoClassDefFoundError`.
- You recently changed **kernel / f2fs / large-folio / compression / atomic-IO** behavior.
- You need to locate **which concrete on-disk files are implicated** and determine whether it’s **storage related**.

If the APK is already known-good and you want to focus directly on the live odex/vdex artifacts, use `references/dex_cnfe_odex_vdex_forensics.md`.

## Core diagnosis rule (fast split)

If the class is reported “not found”, **first check whether it’s actually missing from the APK**.

- If the class **is not in APK dex** → packaging/split install issue (not f2fs).
- If the class **exists in APK dex** → runtime failed to **load dex/oat/vdex** or failed during **mmap/pagefault**; this is the common “storage/kernel” path.

## 1) Pull crash artifacts (DropBox first)

```bash
adb wait-for-device
adb shell su -c 'ls -lt /data/system/dropbox | head -n 50'
adb shell su -c 'ls -1t /data/system/dropbox/system_app_crash@*.txt 2>/dev/null | head -n 10'
adb shell su -c 'ls -1t /data/system/dropbox/data_app_crash@*.txt 2>/dev/null | head -n 10'
```

To pull a specific file:

```bash
adb shell su -c "cat '/data/system/dropbox/system_app_crash@<TS>.txt'" > system_app_crash@<TS>.txt
adb shell su -c "cat '/data/system/dropbox/data_app_crash@<TS>.txt'" > data_app_crash@<TS>.txt
```

Then grep key lines:

```bash
grep -nE 'Process:|Package:|Timestamp:|NoClassDefFoundError|ClassNotFoundException|Caused by:' *.txt
```

## 2) Map crash → APK path (the real file to inspect)

For any crashing package:

```bash
adb shell pm path <pkg.name>
```

Examples:
- `com.UCMobile` → `/data/app/.../com.UCMobile-.../base.apk`
- `com.google.android.apps.nexuslauncher` → `/system_ext/priv-app/.../NexusLauncherRelease.apk`
- `com.android.settings` → `/system_ext/priv-app/.../SettingsGoogle.apk`

## 3) Prove “class exists in APK dex” (host-side)

```bash
apk_path="$(adb shell pm path <pkg.name> | sed -n 's/^package://p' | tr -d '\r')"
adb pull "$apk_path" /tmp/app.apk
python3 - <<'PY'
import zipfile
apk="/tmp/app.apk"
needle=b"Lq53/d;"  # replace with your missing class descriptor
with zipfile.ZipFile(apk,"r") as z:
    dexes=[n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex")]
    hit=[]
    for d in dexes:
        if needle in z.read(d):
            hit.append(d)
    print("dexes:", dexes)
    print("hit:", hit)
PY
```

If `hit` is non-empty, the CNFE is **runtime loading failure**, not packaging.

## 4) Identify runtime-loaded artifacts on `/data` (high correlation to f2fs issues)

### System apps (system_ext) commonly use `/data/dalvik-cache/...`

```bash
adb shell su -c 'find /data/dalvik-cache -type f 2>/dev/null | grep -iE \"(NexusLauncherRelease|SettingsGoogle|SystemUIGoogle)\" | head -n 200'
```

Typical paths:
- `/data/dalvik-cache/arm64/system_ext@priv-app@NexusLauncherRelease@NexusLauncherRelease.apk@classes.dex`
- `/data/dalvik-cache/arm64/system_ext@priv-app@SettingsGoogle@SettingsGoogle.apk@classes.vdex`

### Data apps often have odex/vdex under `/data/app/.../oat/arm64/`

```bash
adb shell pm path <pkg.name>
adb shell su -c "ls -al '$(adb shell pm path <pkg.name> | sed -n 's/^package://p' | tr -d '\r' | xargs dirname)/oat/arm64' 2>/dev/null"
```

## 5) Check “compressed flag” quickly (f2fs `compress_mode=fs` requires per-inode flag)

```bash
adb shell su -c "/system/bin/lsattr '<path-to-file>'"
```

- Look for `c` / `C` style flags (implementation-dependent) to indicate compression.
- If you only see something like `---------E----------`, it’s **not obviously marked compressed** by inode flags.

Note: “atomic write” is usually **not a persistent inode flag** like compression; it’s driven by f2fs ioctls (start/commit). To prove atomic usage, you generally need:
- app-side evidence (who calls the ioctl), or
- kernel-side instrumentation in the f2fs ioctl paths.

## 6) Storage-strong signals to correlate (must check)

### `/data` mount options
```bash
adb shell cat /proc/mounts | grep ' /data '
```

### f2fs warnings / verity failures / I/O errors
```bash
adb shell su -c 'dmesg -T | grep -nE \"F2FS|f2fs|fs-verity|I/O error|dirty_pages|evict_inode|recover_fsync|WARNING:\" | tail -n 250'
```

Look specifically for:
- `WARNING ... f2fs_evict_inode ... inode.c:942` (dirty page invariant)
- `SQLITE_IOERR_FSYNC` / `SQLiteDiskIOException` in logcat (fsync trouble)

## 7) Safe recovery attempt (to get device usable again)

The least destructive “get boot working” fix is to clear compilation artifacts:

```bash
adb shell su -c 'rm -rf /data/dalvik-cache/*'
adb reboot
```

If the problem persists after fresh compilation, it’s strong evidence that your kernel/fs changes broke **mmap/page-fault/dex loading**, not just stale cache.
