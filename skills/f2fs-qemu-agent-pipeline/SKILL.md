---
name: f2fs-qemu-agent-pipeline
description: orchestrate safe and reproducible f2fs or qemu work inside a learn_os style kernel workspace. use when chatgpt needs to source .vars.sh, start qemu without blocking, validate kernel builds, run guest commands, run guest test scripts, verify f2fs/mounts, and collect logs with evidence. default to ssh when available, but if ssh is unavailable/blocked or the user explicitly requests qemu guest agent execution (qga_exec.py), use qga_exec.py as the primary guest command runner.
---

# f2fs / qemu agent pipeline

Follow this skill when working inside a `learn_os` or similar F2FS development workspace where QEMU boot, guest SSH, kernel builds, and filesystem validation must be performed carefully and reproducibly.

## Core behavior

- Treat these instructions as default operating behavior for F2FS, QEMU, guest-SSH, kernel build, and filesystem regression tasks in this workspace.
- Be conservative with destructive actions. Do not reset git state, delete unrelated files, or overwrite user-authored scripts unless explicitly asked.
- Prefer reproducible scripted operations over ad hoc one-off terminal commands.
- Before any build, boot, mount, SSH, or test command, locate the workspace root and source `.vars.sh` if it exists.
- Reuse variables and helper functions exported by `.vars.sh` instead of re-declaring paths manually.
- If required variables or helpers are missing after sourcing, stop and report exactly what is missing.

Use this bootstrap pattern unless the workspace already provides a stronger equivalent:

```bash
set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a
```

## Workflow decision tree

1. **Need to boot or re-boot the guest?** → follow **QEMU start policy**.
2. **Need to run a guest-side command?** → follow **Guest interaction policy**.
3. **Need to validate kernel-side code changes?** → follow **Build policy**.
4. **Need to investigate F2FS behavior, mount effects, or on-disk changes?** → follow **F2FS-focused workflow**.
5. **Need to report results?** → follow **Logging and evidence** and **Safety checks before claiming completion**.

### Directory structure introduction
- `$TEST`  refer to `myscripts/shared_with_qemu/test`has all test script that should run in vm instance.
- `$SCRIPT` refer to `myscripts`has `qemu_start_ori.sh` has `shared_with_qemu` which are shared directory by host and guest using 9pfs.
- `$SCRIPT/guest_console.log` record all guest-side real time log.You should try hard to grep/analyze it.Notice that it is real time log. It changes very fast.You should use wise method to grep/analyze it.

## QEMU start policy

When starting the VM:

- Never run `myscripts/qemu_start_ori` in a way that blocks the chat session.
- Always start QEMU in the background.
- Preserve the caller's launch directory so `guest_console.log` remains in the directory from which the start command was issued.
- Prefer a wrapper such as `.agents/tools/vm_start_bg.sh`.
- After starting QEMU, immediately verify process status and whether SSH on the forwarded guest port is expected to come up.
- Verify the real QEMU process list with `ps aux | grep qemu` or an equivalent process inspection, not only with port checks.
- If the launcher log shows a host-forwarding error such as `Could not set up host forwarding rule`, treat that as a strong signal that another QEMU instance may already be holding the forwarded port.
- In that situation, inspect the existing QEMU process list first and prefer reusing the already-running VM instead of claiming the new launch succeeded.
- Report clearly where the launcher log and guest console log are located.

Default execution pattern:

1. Source `.vars.sh`.
2. Run the background wrapper instead of calling `myscripts/qemu_start_ori` directly.
3. Check that the QEMU process is actually alive with `ps aux | grep qemu` or equivalent process inspection.
4. If launch logs contain host-forward setup failures, directly reuse the existing QEMU instance.

Note: the guest can be controlled either via **SSH** (when available) or via **QEMU Guest Agent (QGA)** using `.agents/tools/qga_exec.py` (when SSH is unavailable/blocked or the user requests QGA).

## QEMU stop policy

When stopping the VM, use a fixed and inspectable pattern instead of ad hoc process killing. The goal is to stop the intended QEMU instance, preserve useful crash evidence when needed, and explain why shutdown is happening.

Use this decision rule:

- **Normal completion / user wants to exit after finishing guest work**: stop the running QEMU instance after confirming the relevant task is done.
- **Kernel crash, panic, or suspected deadlock during testing**: do not kill QEMU immediately. Wait briefly first so console output, panic text, or lockup evidence has time to flush into `guest_console.log`, then stop it.
- **Need to re-launch but ports are still occupied by an old VM**: identify the existing QEMU instance first, decide whether it should be reused, and only stop it if the current workflow really requires a fresh boot.

Required verification and stop pattern:

1. Inspect the real process list with `ps aux | grep qemu`.
2. Identify the target `qemu-system-aarch64` process, not just the wrapper shell.
3. If the stop reason is crash/deadlock related, wait a short period first to collect evidence.
4. Use a fixed stop command pattern against the verified QEMU PID.
5. Re-check `ps aux | grep qemu` afterward to confirm the QEMU process is gone.
6. Report the stop reason, target PID, and whether shutdown completed.

Default stop command pattern:

```bash
ps aux | grep qemu
kill <qemu-system-aarch64-pid>
ps aux | grep qemu
```

Crash/deadlock-oriented variant:

```bash
sleep 5
ps aux | grep qemu
kill <qemu-system-aarch64-pid>
ps aux | grep qemu
```

Important constraints:

- Prefer stopping the `qemu-system-aarch64` PID directly. Do not assume killing the launcher shell is sufficient.
- Do not claim shutdown succeeded unless the post-stop process check confirms that the target QEMU instance disappeared.
- If multiple QEMU instances are present, explicitly state which PID is being stopped and why.
- If the user still needs the guest running, do not stop it just because the skill can.

## Guest interaction policy

This workspace supports two control planes for running commands inside the VM:

- **SSH (default when available)**: best for interactive-ish workflows and bulk file movement.
- **QEMU Guest Agent (QGA) via `.agents/tools/qga_exec.py` (preferred when SSH is unavailable/blocked or when the user explicitly requests it)**.

Treat **`.agents/tools/qga_exec.py` as a high-level tool**: use it instead of ad-hoc `socat`/raw QGA JSON whenever the task is “run a command/script inside the guest”.

### Decision rule (SSH vs QGA)

Use **QGA (`qga_exec.py`)** if any of these are true:

- The user says **SSH is unavailable**, broken, blocked, or intentionally not used.
- The user explicitly requests **`qga_exec.py`** or “QGA execute”.
- The guest has no network / no forwarded port, but QGA is up.

Otherwise, use **SSH** via `.agents/tools/vm_ssh.sh`.

### QGA execution policy (when selected)

1. Verify host-side QGA socket exists and is reachable (typically `/tmp/qga.sock`).
2. Run guest commands via:

```bash
python3 .agents/tools/qga_exec.py '<guest command>'
```

3. For long-running tests or noisy output, do not stream unlimited stdout back through QGA. Prefer:
   - `... | tail -n 200` for quick diagnosis, or
   - redirect logs to a guest-local path (e.g. `/tmp/test.log`) and then `tail` / `sed -n` it.
4. Always capture the exit code and report it.

### SSH execution policy (when selected)

- For non-interactive guest operations, prefer SSH command injection instead of opening an interactive terminal.
- Prefer a wrapper such as `.agents/tools/vm_ssh.sh`.
- Before the first SSH action of a fresh boot, verify the guest is reachable and ready.
- Prefer key-based SSH when it is configured.
- If key-based SSH is not configured, explain the concrete blocker and the smallest next step.
- Prefer idempotent remote commands.
- Do not claim a guest-side step ran unless the SSH command actually succeeded.

Recommended order:

1. Confirm VM is up.
2. Select control plane (QGA vs SSH) using the decision rule above.
3. Verify reachability (QGA socket or SSH port).
4. Run a small probe command.
5. Execute the intended guest command through the chosen wrapper.
6. Capture exit status and any relevant log path.

## Build policy

When asked to validate, test, or sanity-check a kernel-side change:
Loop:
    1. Identify changed `.c` files relevant to the current work using `git diff` or `git status`.
    2. For each changed `.c` file, run the single-file or object-level build helper from `.vars.sh` if available, such as a `kobj`-style helper.
    3. Only after object-level checks succeed, run the full kernel image build command.
    4. Capture build logs to files.
    5. Summarize failures with the exact failing file, target, or phase.
    6. Do not claim success unless the relevant command exited successfully.
    7. Fix small syntax errors that the log reports. Especially be careful when deal with macro bug report.
They can recursive flatten a lot of logs, but many cases the errors of use macro are just small errors.Don't be
confused by the vase error massege.Thinking and reasoning the root case。
Until: no more errors are reported.

When reporting build results, always include:

- the command or script that ran,
- whether it succeeded,
- where the log file is,
- the next recommended step if it failed.

## F2FS-focused workflow

When the task is about F2FS behavior, mount behavior, on-disk effects, regression checks, or guest-visible filesystem state:

- Prefer booting the prepared QEMU guest instead of reasoning purely from source.
- Prefer scripted reproduction steps over informal shell experimentation.
- Keep host-side orchestration on the host and guest-side file operations inside the guest.
- When shared directories or 9p mounts are required, verify the mounts explicitly before proceeding.
- When an encrypted directory path is not confirmed, do not invent one; report that it is unknown.
- If the workflow depends on guest-visible artifacts, verify they actually exist in the guest.


### Tracefs-first debugging pattern

When debugging guest-side page-cache, mmap, readahead, or writeback behavior:

- Prefer running guest commands through [`vm_ssh.sh`](.agents/tools/vm_ssh.sh) with one wrapped remote command block instead of many ad hoc SSH calls.
- Prefer clearing and enabling only the needed tracepoints in `tracefs`, then running the reproduction, then filtering the captured trace before reporting.
- If the bug is inode-specific, always collect the guest file inode with `stat`, convert the decimal inode to hex, and filter trace output by the hex inode because trace events such as `mm_filemap_fault` and `mm_filemap_add_to_page_cache` print inode values in hex.
- Do not rely on `tail` of the whole trace buffer when the task is about a specific file; inode-filtered extraction is the default.
- When a workflow is repeatedly useful, prefer creating or extending reusable wrappers under [`./.agents/tools/`](.agents/tools/vm_ssh.sh) rather than duplicating long command sequences in chat.
- When validating mmap/readahead behavior, capture both MM/filemap tracepoints and filesystem tracepoints when available, because one side alone can hide folio lifecycle transitions.
- Use `$TEST` wisely and based on user needs to validate and continuesly see the logs.
## Logging and evidence

Every meaningful operation should leave evidence when practical:

- the exact command line or wrapper used,
- the exit status,
- a stable log file path when available.

Present results concisely, but never hide the exact failing command.

Use this reporting shape by default:

```text
command/script: <what ran>
status: success | failed | blocked
log: <path or "none">
next step: <smallest useful follow-up>
```

## Safety checks before claiming completion

Before saying the task is done, verify the relevant facts actually happened:

- the build finished successfully,
- the VM is really running if you said it started,
- SSH is really reachable if you said guest access is ready,
- required mount points are present if the task depends on them,
- expected output files or logs were created.

## Refusal to fake progress

- Do not say a VM is up unless you verified it.
- Do not say a kernel built unless the build exited successfully.
- Do not say a guest-side operation ran unless SSH execution actually succeeded.
- If a required script, helper, path, or mount is missing, say so directly and propose the exact missing piece.

## Preferred response style

- Be operational.
- Be explicit about paths, scripts, and next commands.
- Keep summaries short, but include enough detail for manual reproduction.
- When blocked, give the smallest actionable unblock step.

## Guidedance of adding logs to kernel
- Must load skill "kernel-log-instrumentor"

## Example triggers this skill should cover

- “帮我在 learn_os 里把 qemu 起起来，但不要卡住对话。”
- “用ssh操作虚拟机,发送命令和调试”
- “ssh 不可用/不想用 ssh，用 qga_exec.py 在虚拟机里执行命令或跑测试脚本。”
- “先检查这次改动涉及的 f2fs `.c` 文件能不能单独编，再跑整镜像编译。”
- “进 guest 看一下共享目录有没有挂上，顺便跑个非交互命令。”
- “这个 F2FS 改动不要只看代码，帮我进 qemu 做一个可复现验证。”
- “给我一个明确结论：到底是没启动、没连上 ssh，还是编译失败，日志在哪。”

