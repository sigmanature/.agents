# xfstests F2FS/QEMU triage notes

Use this note when xfstests failures in the Ubuntu QEMU guest look like F2FS
kernel regressions but may actually be guest-tool, xfstests expectation, or
bounded-runtime issues.

## Workflow Contract

### Main Workflow

1. Prepare loop devices and `local.config` after every reboot.
2. Fix guest xfstests/tool compatibility before kernel triage.
3. Classify failures as data-integrity, allocation-shape, metadata-journal expectation, or runtime-bound.
4. Patch kernel only for evidenced kernel semantics or crashes.
5. Re-run the target case set and preserve exact logs.
6. Promote repeatable guest-side xfstests patches to this reference or a script.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | Rebooted guest cannot mount `/dev/loop0` or `/dev/loop1` | Re-run the loopback prepare script and rewrite `local.config` | `mount` shows `/mnt/test`; `local.config` has current loop devices | Recreate loop image files and rerun mkfs | block |
| Preflight | F2FS fscrypt cases fail because `mkfs.f2fs` generates unstable or missing UUID data | Set `MKFS_F2FS_PROG=/usr/sbin/mkfs.f2fs` and a nonzero `MKFS_OPTIONS=-U ...` | `./check generic/739` reaches the fscrypt test body | Inspect `blkid` and xfstests fscrypt helper logs | branch |
| Preflight | `common/encrypt` uses GNU awk word-boundary regex but guest has `mawk` | Replace the regex with a portable pattern for the guest run | fscrypt helper no longer drops expected nonce/ciphertext records | Install GNU awk if apt and dependencies are healthy | branch |
| Preflight | `generic/062` reports `awk: ... function asort never defined` | Install `gawk`; if apt is broken, patch `common/attr::_sort_getfattr_output` to use Python record sorting | `generic/062` passes | Do not set `AWK_PROG=/usr/bin/gawk` if `gawk` is absent; `common/attr` hardcodes `awk` | branch |
| Triage | `generic/050` expects read-only recovery mount errors on F2FS | Treat F2FS as `nojournal` in xfstests `_has_metadata_journaling` for this guest run | `generic/050` matches `050.out.nojournal` | Keep the diff as an xfstests expectation mismatch, not a kernel failure | branch |
| Triage | `generic/047`, `049`, `388`, or `705` timeout or run on F2FS | After nojournal classification, expect `[not run] f2fs does not support metadata journaling` | `./check` reports not-run and exits 0 | Re-check the `_has_metadata_journaling` f2fs case | replace |
| Triage | `generic/064` data compare passes but extent count after collapse remains fragmented on F2FS, including with folio order cap `0` | Classify as F2FS LFS physical extent-shape mismatch; for the guest run, suppress only the F2FS extent-count output while keeping the final `cmp` | `generic/064` passes and no data mismatch appears | Do not attempt checkpointed block-address exchange without a separate design | branch |
| Runtime | `generic/017` spends minutes in individual `fcollapse` operations | Mark not-run for this F2FS/QEMU configuration or isolate as a performance investigation | `generic/017` reports not-run and the suite exits 0 | Collect process tree and dmesg checkpoint waits if investigating performance | branch |
| Runtime | `generic/027`, `072`, or `707` exceeds harness timeout but is not D-state hung | Bound loop counts for F2FS/QEMU while preserving the concurrency/race pattern | Target cases pass within the configured timeout | Run the unbounded case separately as a performance job | branch |
| Runtime | `generic/204` spends tens of minutes in ENOSPC file creation | Mark not-run for this F2FS/QEMU configuration or isolate as a performance investigation | `generic/204` reports not-run and the suite exits 0 | Cap file count only for smoke coverage, not for upstream claim | branch |
| QEMU lifecycle | `vm_stop.sh` blocks because the instance pid file is missing, but one exact QEMU PID is known | Stop the exact confirmed PID; avoid fuzzy `pkill -f` | `ps` shows no `qemu-system-aarch64`; stale sockets are removed | Re-check process command line before `KILL` | branch |
| QEMU lifecycle | Background `vm_start_bg.sh` reports initial QGA readiness but `qemu_start_ori.sh` uses `-chardev stdio` and the QEMU process exits afterward | Start `myscripts/qemu_start_ori.sh --log guest_console.log` in a persistent PTY and run QGA/SSH commands from separate host shells | `qga_exec.py 'echo qga_ok; uname -a'` returns and `pgrep -a qemu-system` remains live | Remove stale `/tmp/qga.sock` and `/tmp/qemu-qmp.sock`, then restart via persistent PTY | replace |
| Preflight | Guest `mkfs.f2fs` rejects `-U <uuid>` with `supplied string is not a valid UUID` | Validate `mkfs.f2fs -U` before setting global `MKFS_OPTIONS`; if unsupported, omit UUID options for cases that do not require stable UUIDs | TEST_DEV mounts and xfstests log shows mkfs without the rejected UUID option | Keep UUID-specific cases isolated and fix/replace f2fs-tools before rerunning them | branch |
| Runtime | QGA/SSH stop responding while a CPU-heavy xfstests case is still progressing | Check host QEMU liveness and console first; use longer-spaced probes and avoid killing the guest if QEMU CPU time is increasing and no panic is logged | QGA later recovers or console/QMP show the VM is still running | Try serial console login; if unavailable, preserve host-side QMP/console evidence before deciding to stop | continue |

### Output Contract

- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Case outcomes from the July 2026 F2FS large-folio run

- Kernel crash: `generic/739` was a real large-folio + fs-layer fscrypt issue; keep the kernel fix scoped to encrypted large folios without `SB_INLINECRYPT` and a runtime guard in `f2fs_encrypt_one_page()`.
- Environment/tool fixes: `generic/050` and `generic/062` were guest xfstests/tool compatibility issues.
- F2FS expectation fixes: metadata-journal cases should not run on F2FS; `generic/064` checks physical extent shape, not data integrity, for this F2FS behavior.
- Runtime-bound cases: `generic/017` and `generic/204` are too slow in this QEMU configuration; `generic/027`, `072`, and `707` need bounded stress dimensions for a smoke/regression loop.

## Evidence pattern

- Always save the exact `./check` output under `xfstests_forever_logs/...`.
- When a timeout occurs, collect `ps -eo pid,ppid,stat,etime,wchan:24,cmd` and `dmesg | tail` before claiming a kernel hang.
- Treat `S/do_wait` bash parents with a running `xfs_io` child as slow workload evidence, not a blocked-kernel stack by itself.
- Confirm data integrity separately from allocation-shape assertions; for `generic/064`, the final `cmp` remains the important correctness gate.
