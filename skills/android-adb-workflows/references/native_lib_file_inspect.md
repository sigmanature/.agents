# Inspect `/data/app/.../lib/arm64/*.so` (what file is it, attrs, FS type)

Use this when logcat shows native loader failures like:

- `dlopen failed: can't read file ".../lib/arm64/libmmkv.so": Operation not supported on transport endpoint`
- `Failed to open APK '...': I/O error`

## 0) Get a concrete path from logcat

From your captured logcat text (offline):

```bash
rg -n "Operation not supported on transport endpoint" logcat_all.txt | head -3
```

Copy the full `/data/app/~~.../pkg-.../lib/arm64/<name>.so` path.

If you’re live on device:

```bash
adb -s <SERIAL> logcat -d | rg "Operation not supported on transport endpoint" | head -3
```

## 1) Basic metadata (type/size/SELinux/target)

Run as root (recommended):

```bash
adb -s <SERIAL> shell su -c 'P="<FULL_SO_PATH>";
  echo "P=$P";
  ls -lZ "$P";
  readlink -f "$P" || true;
  toybox stat "$P" 2>/dev/null || stat "$P" || true;
  toybox file "$P" 2>/dev/null || true;
'
```

## 2) Mount type (is it incfs/fuse/ext4/f2fs?)

You want to know what filesystem backs this path (especially whether it’s **incremental FS / incfs** or a **fuse** layer).

```bash
adb -s <SERIAL> shell su -c '
  echo "== mounts (filtered) ==";
  cat /proc/mounts | toybox grep -E " /data |incfs|incremental|fuse" || true;
'
```

If available on your build:

```bash
adb -s <SERIAL> shell su -c 'toybox df -T /data 2>/dev/null || df /data || true'
```

## 3) “lsattr” / FS flags

`lsattr` is implemented via filesystem ioctls; **some FS layers don’t support it** and may return `EOPNOTSUPP`.
That’s useful signal if your issue is “operation not supported …”.

```bash
adb -s <SERIAL> shell su -c 'P="<FULL_SO_PATH>";
  toybox lsattr -a "$P" 2>/dev/null || lsattr -a "$P" 2>/dev/null || echo "lsattr not supported";
'
```

## 4) Quick read test (can the kernel actually read bytes?)

This tells you whether the failure is “can’t open/can’t map/can’t read”.

```bash
adb -s <SERIAL> shell su -c 'P="<FULL_SO_PATH>";
  echo "== dd first 16 bytes ==";
  dd if="$P" bs=16 count=1 2>&1 | head -5;
'
```

If this fails with the same errno/message, treat it as a storage / FS / pagecache signal rather than an app bug.

## 5) Compare two devices (A/B)

Run the same commands on both serials. If only `$FOLIO_S` device shows:

- `Operation not supported on transport endpoint` for `.so`
- `SQLiteDiskIOException` / `I/O error`

…then those exceptions are **not** “common noise” and likely correlate with the large-folio condition.

