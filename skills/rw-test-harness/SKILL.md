---
name: rw-test-harness
description: Use the rw/mmap test harness in /home/nzzhao/learn_os/myscripts/shared_with_qemu/test to run, extend, or refactor buffered I/O and mmap test cases around rw_test.py, rw_matrix.sh, rw_matrix_inline.sh, f2fs_gc_long_rw.py, mkwrite_test.py, and mmap_wp_fault_test.py. Trigger this whenever the user asks to add or modify read/write test cases, matrix cases, long-running GC/writeback stress, inlinecrypt or fscrypt coverage, hole/existing/truncate cases, read-then-write coverage, mmap fault cases, or wants agents to use this framework rather than inventing one-off scripts.
---

# RW Test Harness

Use this skill for the project-local rw test framework under:

- `/home/nzzhao/learn_os/myscripts/shared_with_qemu/test`

The framework has one core provider:

- `rw_test.py`

And a small set of thin frontends:

- `rw_matrix.sh`
- `rw_matrix_inline.sh`
- `f2fs_gc_long_rw.py`
- `case_f2fs_gc_8m_two_phase_image.py`
- `case_f2fs_gc_8m_two_phase_inlinecrypt.py`
- `mkwrite_test.py`
- `mmap_wp_fault_test.py`

## Goal

Keep all future buffered-I/O and mmap testing inside the existing framework instead of creating more one-off scripts or duplicating verifier logic in shell.

## Start Here

1. Open `references/framework-map.md`.
2. Confirm whether the user needs:
   - run existing cases,
   - add buffered-I/O cases,
   - add long-running GC/writeback pressure,
   - add mmap cases,
   - adjust thin shell wrappers,
   - or update the skill itself.
3. Prefer editing `rw_test.py` provider logic first.
4. Keep wrappers thin. Do not reintroduce heredoc Python into shell scripts.

## Workspace hygiene

- When a buffered-I/O, GC, or QEMU experiment needs a source-side branch, prefer `git worktree` rooted from the target branch instead of reusing a dirty main tree.
- Put temporary worktrees and throwaway output directories under `/tmp`.
- If a test helper creates a non-worktree temp directory that should be safe to remove later, drop a `.learn_os_temp_artifact` marker into it.
- Use `scripts/cleanup_learn_os_temp_artifacts.sh` to inspect or delete bounded temp artifacts after the run.

## Framework Rules

### Buffered-I/O family

Use these entrypoints:

- `python3 rw_test.py case ...`
- `python3 rw_test.py matrix ...`
- `./rw_matrix.sh ...`
- `./rw_matrix_inline.sh ...`
- `python3 f2fs_gc_long_rw.py ...`
- `python3 case_f2fs_gc_8m_two_phase_image.py`
- `python3 case_f2fs_gc_8m_two_phase_inlinecrypt.py`

When adding buffered-I/O coverage:

- extend the provider in `rw_test.py`,
- prefer data-driven case additions over ad hoc functions,
- keep `MatrixCase` and builtin matrix generation authoritative,
- add `read_then_write` variants through the existing structured knobs rather than custom pre-read code in shell.
- for GC/writeback long runs, keep environment orchestration in the dedicated runner and reuse `run_matrix_case()` for data verification.
- for the small two-phase GC case, keep GC triggering in `utils/f2fs_gc.py` and use direct `f2fs_io gc_urgent` instead of sysfs writes.
- for post-write fs-verity pressure, treat candidate naming and runtime enablement as separate phases: `.V`-style names only mark stable candidates, while actual `enable_fsverity()` must be checked after each concurrent fsync batch for every batched path, not only the current focus path.
- keep a provider-level regression around that rule in `case/test_artifact_pressure_verity_fsync_plan.py`; do not rely on long guest runs alone to catch batch-wide verity gating bugs.
- when a buffered write stress case needs exact full-file content verification, partition chunk ownership across concurrent writers first; if `workers > slots`, reject exact-content verification and fall back to invariant-only checks instead of emitting a false corruption signal.
- when mounted verification still reports a mismatch after disjoint ownership is in place, preserve the loop image and confirm the same bytes through a fresh read-only remount before concluding it is an on-disk corruption symptom.

### Inline Artifact Pressure Workflow Contract

#### Main Workflow
1. Before running `case/test_inlinecrypt_artifact_pressure.py`, check where `INLINE_ARTIFACT_WORKDIR` resolves and treat `WORKDIR/f2fs.img` as guest rootfs usage unless the workdir was explicitly moved elsewhere.
2. If prior failed runs exist under `/tmp/inline_verity_pressure_qga/run_*`, pull the failing sample files and a failure manifest to the host before any cleanup.
3. Reclaim guest rootfs space only after evidence is preserved, then launch or relaunch the pressure loop.
4. On `SIGBUS` or corruption, correlate the saved sample, `process_meta.json`, and kernel logs before changing the workload.

#### Decision Table
| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | `test_inlinecrypt_artifact_pressure.py` is using its default `WORKDIR` path or a custom path under guest `/tmp` | Check guest `df -h /`, inspect `/tmp/inline_verity_pressure_qga/run_*`, and remember that the loop image lives at `WORKDIR/f2fs.img` | Guest rootfs has enough free space for a new 2G image plus evidence files, or old runs have been preserved and removed | Pull failure samples first, then delete old run dirs and/or vacuum guest journals before relaunch | block |
| Evidence | Previous failed run dirs already exist on guest rootfs | Pull at least the failing tmp/sample file, stage log, metadata, and a manifest of failure directories to the host before deleting anything | Host-side size/hash or comparable integrity check matches the guest files | Keep the guest stopped at evidence collection and do not relaunch until host copies exist | replace |

#### Output Contract
- phase reached:
- guest workdir path:
- guest rootfs usage before run:
- evidence preserved to host:
- cleanup applied:
- workload pid / loop status:
- unresolved blocker:

### mmap family

Use these entrypoints:

- `python3 rw_test.py mmap-case ...`
- `python3 rw_test.py mmap-matrix ...`
- `python3 mkwrite_test.py ...`
- `python3 mmap_wp_fault_test.py ...`

When adding mmap coverage:

- add the reusable case implementation to `rw_test.py`,
- register the case in the builtin mmap case registry,
- keep `mkwrite_test.py` and `mmap_wp_fault_test.py` as thin frontends unless they truly need specialized behavior,
- keep trace-specific reporting separate from generic case execution when possible.
- when a `MAP_SHARED` writer dies with `SIGBUS` under f2fs pressure, inspect `page_mkwrite_state`, `reserve_block`, and `get_block_locked` for the failing inode before assuming post-writeback corruption or EOF/truncate; large-folio faults can reserve several subpages as `NEW_ADDR` and then fail a later subpage with `err=-28`, leaving a zero tail in the saved sample.

## Editing Policy

1. Prefer extending `rw_test.py` provider helpers and case registries.
2. Only touch shell wrappers for environment selection, root bootstrap, mount checks, or argument forwarding.
3. If a repeated command sequence appears, consider whether it belongs in:
   - the skill references,
   - the framework itself,
   - or a reusable script.
4. Do not fork parallel test architectures inside the same repo.
5. Prefer a separate temporary build/output directory over reusing a persistent output tree when you are validating risky or noisy temporary changes.
6. When parse/pattern/readback/disk-verify logic becomes reusable across runners, move it into `utils/` rather than keeping it private inside `rw_test.py`.

## Validation Workflow

After changes, prefer this order:

1. `python3 -m py_compile rw_test.py f2fs_gc_long_rw.py mkwrite_test.py mmap_wp_fault_test.py`
2. targeted `rw_test.py case` or `rw_test.py mmap-case` smoke run on `/tmp`
3. targeted `rw_test.py matrix` or `rw_test.py mmap-matrix` smoke run on `/tmp`
4. `bash -n rw_matrix.sh`
5. `bash -n rw_matrix_inline.sh`
6. `python3 f2fs_gc_long_rw.py --allow-plain-only --runtime-sec 5 --case-filter o0_aligned`
7. only then run real f2fs/inlinecrypt/tracefs environments if needed

If root, fscrypt, inlinecrypt, or tracefs are unavailable, still complete static validation and local smoke tests, then state that environment validation is pending.

## Output Contract

When working with this framework:

- say which family you changed: buffered-I/O, mmap, wrappers, or skill
- name the command(s) you used for validation
- call out any unverified root-only or tracefs-only paths
- mention whether you changed provider logic or only frontends

## References

- `references/framework-map.md`
- `references/case-authoring.md`
- `references/gc-long-run-targets.md`
- `/home/nzzhao/learn_os/references/worktree-temp-artifact-hygiene-20260402.md`

## Evaluation Prompts

See `evals/evals.json` for realistic prompts that should trigger this skill.
