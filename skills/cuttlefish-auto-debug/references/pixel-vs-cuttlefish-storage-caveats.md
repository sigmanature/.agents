# Pixel vs Cuttlefish storage caveats

Cuttlefish is not a drop-in reproduction of Pixel 6 storage hardware.

Known differences that can matter for an F2FS / inlinecrypt / SQLite fsync failure:

- Pixel `/data` can be f2fs on dm with `inlinecrypt`; Cuttlefish images may use different block devices, encryption capabilities, and filesystem options.
- If Cuttlefish has no inline encryption hardware support but the kernel enables blk-crypto fallback, encrypted I/O can still proceed through software crypto fallback.
- Software fallback can alter write I/O shape because encrypted writes may use bounce pages and can affect bio splitting/merging/timing.
- Cuttlefish is still valuable because it restores observability: adb root, tracefs, raw syscalls, f2fs/block tracepoints, and perfetto traces.

Use it first to capture upper-layer syscall and scheduling sequence, then decide whether to make Cuttlefish storage closer to Pixel.
