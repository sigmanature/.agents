---
name: kernel-debug-orchestrator
description: Orchestrate iterative Linux kernel debugging in learn_os style workspaces by chaining QEMU bring-up, targeted instrumentation, build, test execution, and evidence-driven next-iteration decisions. Use this when tasks are kernel debugging loops (for example writeback/f2fs/fscrypt crashes), and you need a repeatable cycle that depends on f2fs-qemu-agent-pipeline plus kernel-log-instrumentor.
---

# Kernel Debug Orchestrator

Use this skill for end-to-end kernel debug loops where you must repeatedly:

1. start/verify guest runtime,
2. add or refine kernel logs,
3. build,
4. run reproduction tests,
5. analyze logs,
6. update instrumentation and repeat.

## Hard dependencies (fixed)

Always load these skills in this order:

1. `f2fs-qemu-agent-pipeline`
2. `kernel-log-instrumentor`

Optional third skill based on test type:

- `xfstests-qga-ubuntu` when the reproduction is xfstests/QGA-only.

Do not skip the first two dependencies for this skill.

## Scope and assumptions

- Workspace resembles `learn_os` with `.vars.sh` and QEMU helpers under `.agents/tools/`.
- Kernel source is a git repo (for temporary debug branch + one reversible commit).
- Guest access may use SSH or QGA; choose per `f2fs-qemu-agent-pipeline` policy.

## Orchestration loop

### Step 0: Normalize context

- Source `.vars.sh`.
- Identify `source tree` (e.g. `$BASE/f2fs`) and `O=` build tree (e.g. `$BASE/f2fs_upstream`).
- Confirm target function(s), subsystem, and reproduction script.

### Step 1: Boot and verify VM readiness

- Start QEMU in non-blocking/reusable way per `f2fs-qemu-agent-pipeline`.
- Verify process, control plane reachability (QGA/SSH), required mounts, and test paths.
- Record command + status + evidence path.

### Step 2: Instrumentation planning

Use `kernel-log-instrumentor` rules:

- create a temporary debug branch,
- keep logs in one commit,
- default to `pr_debug` + dynamic_debug,
- include `__func__` and stable log prefix.

Guardrail: new log lines must not introduce fresh pointer dereference risk. Prefer printing raw pointers/flags/scalars first.

### Step 3: Apply instrumentation

- Patch minimal callsites around suspected failure path (entry, branch decision, error path, state transition).
- For concurrency/state counters, log before/after updates with clear labels.

### Step 4: Build verification

- Object-level checks first (for changed `.c` files, use `kobj` from `.vars.sh`).
- Then full image build (`bash $SCRIPT/make_upstream.sh`).
- Report exact build log path and pass/fail.

Pixel/Slider variant:

- If the workspace uses `private/google-modules/soc/gs/build_slider.sh`, run it from the `pixel/` repo root, not from `private/google-modules/soc/gs/`.
- Reason: the script executes `tools/bazel` via a cwd-relative path; invoking it from the subdirectory fails before compilation with `tools/bazel: No such file or directory`.
- If Bazel dies during server startup with `channel not registered to an event loop`, classify it as a build-environment blocker first, not a source compile result. Capture the `out/bazel/.../server/jvm.out` path in the report.

### Step 5: Run reproduction

Support two reproduction modes:

1. Existing script mode:
   - run the known test script (e.g. `rw_matrix.sh`) with deterministic env.
2. On-demand script mode:
   - create a minimal one-off reproducer under `$TEST` when no usable script exists.

For long tests via QGA:

- redirect output to guest file,
- tail/log-scan separately,
- never equate QGA timeout with test failure until process/log state is checked.

### Step 6: Collect and correlate evidence

Collect at minimum:

- test stdout/stderr log,
- kernel console log (`guest_console.log`),
- filtered debug lines by stable prefix,
- first failure stack trace and surrounding window.

Correlation requirement:

- identify the last N debug lines before first Oops/BUG,
- map to function + decision branch + key state fields.
- when `sysrq` dumps are present, run pid/ino correlation script:
  - `/home/nzzhao/learn_os/scripts/f2fs_pid_ino_correlate.sh <kernel_stream.txt> 3 40`
  - use output to align blocked `pid` with nearby `[WBDBG] pid/ino` activity.
  - prioritize comm-cluster evidence (`PackageManager*`, `android.bg`, `android.io`) when same-pid WBDBG is sparse.

### Step 7: Decide next iteration

Classify and act:

- `insufficient signal`: add/refine logs and repeat from Step 2.
- `clear root-cause candidate`: propose fix patch and validate with same repro.
- `non-deterministic`: tighten repro and add ordering/state logs.

Keep each iteration bounded; avoid broad logging expansion without evidence.

## Output contract per iteration

Use this structure every loop:

- `iteration`: integer
- `goal`: what this round tries to prove/disprove
- `instrumentation`: files/functions and why
- `build`: command + status + log path
- `repro`: command + status + log path
- `findings`: concrete evidence and inference
- `next action`: smallest high-value next step

## Stop conditions

Stop loop only when one is true:

1. root cause is evidenced with high confidence and patch direction is clear, or
2. current instrumentation cannot progress and a specific external dependency is missing.

If blocked, report exact blocker and minimal unblock command.

## Reference playbooks

- `references/f2fs-write-end-io-playbook.md` for writeback/compression/folio-private style crashes.
- `references/f2fs-pid-ino-correlation-playbook.md` for sysrq blocked-stack and WBDBG pid/ino correlation.

## Lessons learned (QGA and debug safety)

1. QGA command channel is effectively serialized for this workflow.
   - Do not launch multiple long `qga_exec.py` commands in parallel.
   - Treat one long-running QGA command as owning the channel until it exits.
2. For long repros, always run in guest background and poll.
   - Start once with guest-side redirection to a fixed log file.
   - Persist exit code to a separate file (for example `/tmp/<case>.rc`).
   - Poll with short QGA queries (`ps`, `tail`, rc-file check).
3. Avoid host-shell expansion bugs in QGA payloads.
   - Use escaped guest variables (`\$log`, `\$?`) or fixed file names.
   - Validate generated command string before dispatch.
4. Logging instrumentation must be null-safe by construction.
   - Never add debug prints that dereference pointers before null/ERR checks.
   - For bounce/compress pointer transitions, guard with `IS_ERR_OR_NULL` before any field access.
5. A crash after adding logs may be caused by the log path itself.
   - If PC lands in `__dynamic_pr_debug` callsite area, suspect log argument dereference first.
   - Harden logs, rebuild, and re-run before escalating root-cause claims.
6. QEMU launch reliability: verify process, not launcher text.
   - `nohup qemu_start_ori.sh ...` may return config banner but still fail to leave a live `qemu-system-aarch64` process.
   - Always confirm with `ps` and fallback to a persistent PTY-backed launcher session when needed.
7. Pixel slider build entrypoint is cwd-sensitive.
   - `private/google-modules/soc/gs/build_slider.sh` must be launched from the `pixel/` repo root.
   - A failure at `tools/bazel: No such file or directory` is an invocation-path issue, not a kernel build failure.
