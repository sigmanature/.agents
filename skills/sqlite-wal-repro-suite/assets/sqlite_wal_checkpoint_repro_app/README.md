# sqlite_wal_checkpoint_repro_app

Minimal **debuggable** Android app + **Instrumentation** workload to reproduce:
- SQLite `journal_mode=WAL`
- explicit `PRAGMA wal_checkpoint(TRUNCATE)`
- frequent `PRAGMA quick_check` (fail-fast corruption detection)
- deterministic payload pattern (for semantic verification)
- snapshot `db/wal/shm` on first failure to external files (easy `adb pull`)

This is designed for Pixel devices where app data lives under:
`/data/user/0/<pkg>/databases` (fscrypt / CE path).

## Packages

- App package: `com.learnos.sqlitewalrepro`
- Instrumentation runner: `androidx.test.runner.AndroidJUnitRunner`
- Test package: `com.learnos.sqlitewalrepro.test`

## Build (host)

Requires Android SDK + JDK (AGP downloads deps from internet).

```bash
cd android/sqlite_wal_checkpoint_repro_app
./gradlew :app:assembleDebug :app:assembleAndroidTest
```

## Install (device)

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
```

## Run (device)

Basic (5 minutes, TRUNCATE checkpoint, 4KiB payload, check every loop):

```bash
adb shell am instrument -w -r \\
  -e seconds 300 \\
  -e checkpoint TRUNCATE \\
  -e synchronous FULL \\
  -e blobBytes 4096 \\
  -e updatesPerTxn 200 \\
  -e rows 2048 \\
  -e checkEvery 1 \\
  -e patternSample 10 \\
  com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner
```

More stress (multiple writers + readers):

```bash
adb shell am instrument -w -r \\
  -e seconds 600 \\
  -e writers 2 \\
  -e readers 2 \\
  -e checkpoint TRUNCATE \\
  -e checkEvery 1 \\
  com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner
```

## Artifacts

On first failure (quick_check != ok or pattern check fails), the test:
- logs a `FAIL` line (logcat) with `ts_mono_ns` + `iter`
- snapshots these files:
  - `repro.db`
  - `repro.db-wal`
  - `repro.db-shm`

Artifacts are copied to:
- `/sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts/<run_id>/`

Pull:

```bash
adb pull /sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts ./wal_repro_artifacts
```

## Logcat filtering

```bash
adb logcat -v threadtime | grep -F 'WalRepro'
```

The log lines are table-friendly `k=v` rows with:
- `ts_mono_ns` (SystemClock.elapsedRealtimeNanos) for Perfetto alignment
- `iter` / `phase` / `tid`
- `db_size` / `wal_size` / `shm_size`

## Implementation note (Android SQLite PRAGMA)

Some PRAGMAs (notably `PRAGMA journal_mode=...`) return result rows.
On Android, `SQLiteDatabase.execSQL()` rejects statements that can return rows.
This repro uses `rawQuery(...).close()` for PRAGMA statements to avoid:
`Queries can be performed using SQLiteDatabase query or rawQuery methods only.`
