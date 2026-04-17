# SQLite WAL checkpoint repro app (Instrumentation)

This note documents how to run the **Java/Kotlin Instrumentation** repro app:
`~/learn_os/android/sqlite_wal_checkpoint_repro_app/`

Goal:
- reproduce **WAL + explicit checkpoint** writeback patterns under **fscrypt app data path**
- detect first corruption quickly (`PRAGMA quick_check`)
- snapshot `db / db-wal / db-shm` at first failure
- produce time-aligned logs for Perfetto (`elapsedRealtimeNanos`)

## Entrypoint (source)

This project does **not** define an Activity/Service entrypoint in `AndroidManifest.xml`.
The workload entry is an Instrumentation test method:

- `android/sqlite_wal_checkpoint_repro_app/app/src/androidTest/java/com/learnos/sqlitewalrepro/WalCheckpointReproTest.java`
  - class: `com.learnos.sqlitewalrepro.WalCheckpointReproTest`
  - method: `runWalCheckpointLoop()` (`@Test`)

Runner wiring:
- `android/sqlite_wal_checkpoint_repro_app/app/build.gradle` sets `testInstrumentationRunner "androidx.test.runner.AndroidJUnitRunner"`

## SQLite open + PRAGMA configuration (what affects WAL/checkpoint)

Open flags:
- `Context.openOrCreateDatabase(dbName, openMode, null)`
  - `openMode = MODE_PRIVATE | MODE_ENABLE_WRITE_AHEAD_LOGGING`

WAL enabling (done once on the init connection, before threads open their connections):
- `SQLiteDatabase.enableWriteAheadLogging()`
- `PRAGMA journal_mode=WAL` (executed via `rawQuery(...).close()`; Android `execSQL()` rejects statements that can return rows)

Per-connection configuration (applied to init + every worker connection):
- `PRAGMA wal_autocheckpoint=0` (disable auto checkpoint; relies on explicit checkpoint)
- `PRAGMA mmap_size=0` (disable mmap; forces traditional read/write IO paths)
- `PRAGMA synchronous=<arg>` (default `FULL`, configurable via `-e synchronous ...`)

Per-iteration checkpoint:
- `PRAGMA wal_checkpoint(<mode>)` where `<mode>` comes from `-e checkpoint ...` (default `TRUNCATE`)

Notes:
- `journal_size_limit` is not set by this repro (SQLite default behavior applies).
- `busy_timeout` is not set.

## Workload structure (high-level)

Threads:
- 1+ writer threads: each holds its own SQLite connection, loops `BEGIN; UPDATE...; COMMIT; wal_checkpoint(...)`
- optional reader threads: random `SELECT` pressure
- 1 checker thread: periodic `PRAGMA quick_check` + sampled CRC pattern verification

Failure artifacts:
- On first failure, the test copies `db/db-wal/db-shm` to external files and calls `FileOutputStream.getFD().sync()` to force durability of the snapshot.

## Build on host

The project uses Gradle + Android Gradle Plugin, requiring:
- JDK installed (`java` available)
- Android SDK installed (`ANDROID_HOME` or `sdk.dir` in `local.properties`)
- network access for dependency downloads (first time)

```bash
cd /home/nzzhao/learn_os/android/sqlite_wal_checkpoint_repro_app
./gradlew :app:assembleDebug :app:assembleAndroidTest
```

## Install on device

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
```

## Run on device

Baseline (single writer + checker, checkpoint every loop):

```bash
adb shell am instrument -w -r \
  -e seconds 300 \
  -e checkpoint TRUNCATE \
  -e synchronous FULL \
  -e blobBytes 4096 \
  -e updatesPerTxn 200 \
  -e rows 2048 \
  -e checkEvery 1 \
  -e patternSample 10 \
  com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner
```

More stress:

```bash
adb shell am instrument -w -r \
  -e seconds 600 \
  -e writers 2 \
  -e readers 2 \
  -e checkpoint TRUNCATE \
  -e checkEvery 1 \
  com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner
```

## How corruption is detected (golden criteria)

The app treats **either** as failure:
- `PRAGMA quick_check` returns non-`ok` (SQLite internal consistency)
- pattern check detects `crc_mismatch` for sampled rows (semantic corruption)

On first failure it snapshots:
- `repro.db`
- `repro.db-wal`
- `repro.db-shm`

to:
`/sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts/<run>/<iter_...>/`

Pull:

```bash
adb pull /sdcard/Android/data/com.learnos.sqlitewalrepro/files/wal_repro_artifacts ./wal_repro_artifacts
```

## Log alignment with Perfetto

Logcat tag: `WalRepro`

Each phase prints `ts_mono_ns=SystemClock.elapsedRealtimeNanos()` which aligns well to
Perfetto ftrace timestamps (since-boot semantics).
