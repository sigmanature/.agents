# Offline logcat compare (two devices)

This note is for the “I already collected `logcat_all.txt` from 2 devices; show me what differs, and which crashes are filesystem-related” workflow.

## 1) Identify device folders

Typical layout:

- `<cap_root>/<SERIAL>/session_<YYYY-MM-DD_HHMMSS>/logcat_all.txt`

## 2) Find “app keeps stopping” signals (crash + activity)

In logcat, the most useful trio is:

1) **Launch** (which activity was started):

```bash
rg -n "ActivityTaskManager: START" logcat_all.txt | head
rg -n "ActivityTaskManager: START.*cmp=<pkg>" logcat_all.txt | head
```

2) **Crash** (events buffer style):

```bash
rg -n "am_crash:" logcat_all.txt | head
rg -n "am_crash: .*<pkg>" logcat_all.txt
```

3) **Fatal stack** (main buffer style):

```bash
rg -n "AndroidRuntime: FATAL EXCEPTION" logcat_all.txt | head
rg -n "AndroidRuntime: FATAL EXCEPTION|FATAL EXCEPTION" logcat_all.txt | head
```

Optional, sometimes present:

```bash
rg -n "Force finishing activity" logcat_all.txt | head
```

## 3) Filesystem-ish crash signatures to watch

These strings are high-signal for storage / VFS / pagecache issues (and often correlate with “apps keep stopping”):

```bash
rg -n "Operation not supported on transport endpoint" logcat_all.txt | head
rg -n "SQLiteDiskIOException|disk I/O error" logcat_all.txt | head
rg -n "Failed to open APK.*: I/O error" logcat_all.txt | head
```

## 4) Compare two devices (A/B)

### A) Quick “unique” check (filesystem-ish)

```bash
rg -n "Operation not supported on transport endpoint|SQLiteDiskIOException|Failed to open APK.*: I/O error" <A/logcat_all.txt> | head
rg -n "Operation not supported on transport endpoint|SQLiteDiskIOException|Failed to open APK.*: I/O error" <B/logcat_all.txt> | head
```

If these appear only on one device, treat them as likely related to the device-only condition (e.g., large folio enablement).

### B) Common “noise” (ignore if present on both)

When doing large-scale app launches (monkey / batch install), you often see missing-library warnings that are unrelated to filesystem bugs:

- `... gxp_host_late_binding.cc:839] FAILED_PRECONDITION: dlopen failed: library "libgxp.so" not found`
- `nativeloader: ... dlopen failed: library "libbrotli.so" not found`

If a signature appears on **both** devices with similar frequency, deprioritize it as a root-cause candidate.

## 5) Helper script (optional)

If you want an automated “common vs unique crash signature” summary:

```bash
python3 scripts/logcat_sig_diff.py --a <A/logcat_all.txt> --b <B/logcat_all.txt>
```

