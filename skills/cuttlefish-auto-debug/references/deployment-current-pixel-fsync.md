# Deployment notes for current Pixel fsync repro investigation

Workspace described by user:

- Pixel repo root: `/home/nzzhao/learn_os/pixel`
- Kernel common tree: `/home/nzzhao/learn_os/pixel/common`
- Current branch: `debug/pkgxml_einval_trace_20260408`
- Current commit: `5c746c226d36`
- Relevant fragment: `common/arch/arm64/configs/f2fs_large_folio.fragment`
- Physical device: Pixel 6 / oriole / Android 16 user build
- Physical failure: `SQLITE_IOERR_FSYNC` under SQLite WAL + `wal_checkpoint(TRUNCATE)` workload

Primary deployment objective:

1. Use Cuttlefish userdebug to restore tracing observability.
2. Capture syscall sequence with perfetto/ftrace.
3. Only after the pipeline is stable, customize the Cuttlefish kernel/storage path if necessary.

Do not assume `/home/nzzhao/learn_os/pixel/out/dist` contains Cuttlefish platform images. If it does not contain `cvd-host_package.tar.gz` and `aosp_cf_*_img*.zip`, locate a platform build dist or fetch/build Cuttlefish images separately.
