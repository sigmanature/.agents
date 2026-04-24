# Display Black Screen With SurfaceFlinger Alive

Use this note when the device is black-screened but you need to distinguish:

- display stack never started
- panel/composer path is broken
- SurfaceFlinger is alive but the composed output is intentionally black
- `system_server` is hung or dead and left display state stuck

## Fast split

1. Check whether the display stack is up:

```bash
SER=<SERIAL>
adb -s "$SER" shell 'pidof surfaceflinger; ps -A -o PID,PPID,STAT,NAME,ARGS | grep -E "surfaceflinger|composer|system_server"'
adb -s "$SER" shell 'service check SurfaceFlinger; service check display'
```

2. Capture what SurfaceFlinger is actually producing:

```bash
mkdir -p /tmp/display_triage
adb -s "$SER" exec-out screencap -p > /tmp/display_triage/current.png
file /tmp/display_triage/current.png
```

Interpretation:
- physical panel black, screenshot normal: compositor/panel/backlight path
- screenshot black too: SurfaceFlinger output is black, so keep debugging in SF/display state/system_server

3. Check for a stuck full-screen black layer or brightness clamp:

```bash
adb -s "$SER" shell dumpsys SurfaceFlinger 2>/dev/null | \
  grep -n -C 3 -E 'ColorFade|BrightnessController|brightness level|displayBrightness|Display 0'
```

High-signal clues:
- full-screen `ColorFade` / `ColorFade BLAST` visible on Display 0
- `BrightnessController` shows `brightness level 0`
- `Display 0` / HWC state still exists, proving the display path started

## If SurfaceFlinger is alive, pivot to `system_server` and `/data`

When `dumpsys activity` / `dumpsys display` time out or `system_server` looks unhealthy, check for watchdog + I/O stalls:

```bash
adb -s "$SER" logcat -b all -d -v threadtime | \
  grep -E 'WATCHDOG KILLING SYSTEM PROCESS|FileDescriptor.sync|PackageInstallerService.writeSessions|screen_toggled|boot_progress_enable_screen'

adb -s "$SER" shell 'su -c "dmesg | grep -i -E \"f2fs|fsync|writeback|I/O error|watchdog|hung task\" | tail -n 300"'

adb -s "$SER" shell 'su -c "ls -lt /data/system/dropbox | head -n 20"'
adb -s "$SER" shell 'su -c "gzip -dc /data/system/dropbox/system_server_pre_watchdog@*.txt.gz | sed -n 1,200p"'
adb -s "$SER" shell 'su -c "gzip -dc /data/system/dropbox/system_server_watchdog@*.txt.gz | sed -n 1,220p"'
```

What matters:
- watchdog subject says `Blocked in handler on i/o thread (android.io)`
- Java stack includes `FileDescriptor.sync` and `PackageInstallerService.writeSessions`
- waiting channels show `folio_wait_bit_common` or similar writeback waits
- kernel log shows `f2fs_*sync*`, writeback waits, or repeated F2FS warnings

This combination means the black screen is usually a secondary symptom: `system_server` got stuck on `/data` I/O, and the display state stayed black because the restore path never completed.

## File-corruption check for the display executables

If the user suspects storage corruption or zero-filled files, pull the relevant display ELF files and scan for full zero pages on the host:

```bash
mkdir -p /tmp/display_triage/files
for f in \
  /system/bin/surfaceflinger \
  /vendor/bin/hw/android.hardware.graphics.composer@2.4-service \
  /vendor/lib64/hw/hwcomposer.gs101.so \
  /vendor/lib64/libhwc2on1adapter.so \
  /system/lib64/libgui.so
do
  adb -s "$SER" exec-out su -c "cat '$f'" > "/tmp/display_triage/files/$(basename "$f")"
done
```

Then scan for full 4 KiB zero pages or suspicious long zero runs. If there are no page-sized zero regions in the display binaries, that weakens the “display service binary got zero-filled” theory. Continue on the `/data` / watchdog path instead.

## Case pattern captured from a Pixel 6 / Android 16 incident

- `surfaceflinger` and `android.hardware.graphics.composer@2.4-service` were both running
- `screencap` was fully black
- SurfaceFlinger showed a visible full-screen `ColorFade BLAST`
- `BrightnessController` reported `brightness level 0`
- logcat showed `Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in handler on i/o thread (android.io) for 68s`
- the Java stack pointed at `FileDescriptor.sync` inside `PackageInstallerService.writeSessions`
- `dmesg` showed F2FS warnings and `f2fs_*sync*` / writeback waits
- pulled display ELF files did not show full-page zero-fill

Treat that pattern as: display stack started, but `system_server` and `/data` I/O trouble left the device in a black display state.
