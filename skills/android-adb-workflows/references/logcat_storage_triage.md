# Offline logcat “storage regression” triage

This note is for quickly answering a question like:

> “Is this crash likely caused by filesystem / compression / mmap / large-folio, or is it an app-level Dex/ClassLoader issue?”

## Use the helper script

From any directory on your host:

```bash
python3 ~/.agents/skills/android-adb-workflows/scripts/logcat_storage_triage.py \
  --log <path/to/logcat_all.txt> \
  --top 40
```

To zoom into a specific process/package and print nearby lines for each hit:

```bash
python3 ~/.agents/skills/android-adb-workflows/scripts/logcat_storage_triage.py \
  --log <path/to/logcat_all.txt> \
  --focus com.UCMobile \
  --focus-context 60
```

## What counts as “storage-ish evidence”

Strong signals (high value):
- `SIGBUS` (file-backed mapping read faults)
- `Input/output error` / `I/O error` / `EIO`
- `ZipException`, zip/dex/open failures (APK/Dex cannot be mapped/read)
- `SQLiteDiskIOException`, `sqlite_db_corrupt:` (often symptoms of I/O failures or unclean shutdown; investigate further)
- Kernel fs lines bridged into logcat (e.g. `F2FS-fs`, `EXT4-fs`, journal errors)

Medium signals (needs context):
- SELinux `avc: denied` involving `/data/...` paths or `dev="dm-XX"`
  - This is *permission/policy*, not a filesystem corruption by itself, but it can cause “file not found / open failed” behavior for services.
- Repeated `Permission denied` / `No such file or directory` for `/data/...`
  - Might be benign (missing optional files) or policy-related; check who is trying to read/write.

Weak signals (often noise):
- `/proc/...` or `/sys/...` write failures from perf/thermal daemons (not storage; kernel config / permissions).

## Heuristic interpretation rule

If you suspect a **large-folio + compression + file-backed mmap** regression, you usually see **at least one** of:
- kernel fs errors, OR
- explicit `mmap`/zip/dex open failures, OR
- `SIGBUS` / “bus error” style crashes.

If you only see:
- `ClassNotFoundException`, `NoClassDefFoundError`, `VerifyError`, etc.,
and *no* I/O-related logging, that points more strongly to:
- app packaging / split APK / hotfix framework issues (Tinker/Qigsaw/etc.),
- stale dalvik-cache artifacts,
- or a genuine app compatibility problem on that Android version.

