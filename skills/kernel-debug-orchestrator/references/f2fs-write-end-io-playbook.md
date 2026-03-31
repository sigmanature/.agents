# F2FS Write-End-IO Playbook

Use this playbook when symptoms involve:

- `f2fs_write_end_io`
- `f2fs_is_compressed_folio`
- `__has_merged_page` / `__submit_merged_write_cond`
- writeback + fsync triggered crashes in encrypted/compressed paths

## Preflight

1. Verify VM control plane (QGA or SSH).
2. Verify test directory assumptions (for fscrypt flows):
   - `fscrypt status <enc_dir>`
   - `lsattr -d <enc_dir>` includes `E`
3. Enable dynamic debug for touched files.

## Instrumentation baseline

Add low-risk `pr_debug` at:

1. function entry with pointer + flags + index,
2. branch points before/after compression checks,
3. writeback completion and pending counter updates,
4. error path where bio status is not `BLK_STS_OK`.

Avoid new dereference of potentially invalid pointers in logs.

## Reproduction guidance

- Prefer existing matrix script first (`rw_matrix.sh` style).
- If signal too noisy, create a minimal reproducer that only does:
  - create/truncate,
  - fsync-heavy overwrite/append,
  - repeat on plain + encrypted directories.

## Evidence extraction

For each crash:

1. first Oops line and timestamp,
2. first call trace with top 5 frames,
3. last debug lines in target functions before Oops,
4. state summary (`private`, flags, mapping/index, pending counters).

## Hypothesis framing

- If `has_private=1` but `private` looks invalid/null: suspect private-state corruption or lifecycle race.
- If `nonptr`/`has_ffs` flags disagree with expected path: suspect state transition mismatch.
- If crash only appears on fsync/writeback edge: prioritize merged-write and completion boundary checks.

## Iteration upgrade rules

- If baseline logs still miss transition cause, add one layer deeper only:
  - caller-side log before invoking failing helper,
  - state log immediately after state mutation APIs.
- Do not spread logs to unrelated subsystems before exhausting local path.

## QGA execution pattern (required for long loops)

1. Start repro in background from guest shell:
   - write stdout/stderr to `/tmp/<case>.log`
   - write exit code to `/tmp/<case>.rc`
2. Poll with short commands only:
   - `ps -ef | grep <case>`
   - `tail -n 80 /tmp/<case>.log`
   - `cat /tmp/<case>.rc` (exists => done)
3. Do not issue a second long QGA job while one is active.
4. If output appears stuck, first check for a still-running guest process before concluding timeout/failure.

## Logging hardening checklist

Before enabling dynamic debug on new code:

1. Guard candidate pointers with `NULL/ERR` checks before field access.
2. For transformed pointers (bounce folio/compress control folio), re-check validity after each transformation.
3. Print only pointer values and scalar flags in early probes; avoid nested dereference until validated.
4. When crash PC points near debug print callsites, treat instrumentation as suspect and validate null-safety first.

## Additional lessons learned (2026-03-27)

1. Avoid adding debug print arguments that call `folio_test_private()` directly when folio validity is under suspicion; instrumentation itself can become the first crashing site.
2. For early `f2fs_write_end_io()` failures, prefer a `noinline` wrapper around `iostat_update_and_unbind_ctx()` and log only pointer-level state (`bio`, `bi_private`, `bi_status`) before/after the call.
3. In this workspace, if `nohup qemu_start_ori.sh ...` does not leave a live `qemu-system-aarch64` process, fall back to a persistent PTY-backed launcher session and verify with `ps` immediately.
