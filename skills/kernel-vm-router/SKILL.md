---
name: kernel-vm-router
description: Use when a Linux kernel task is primarily about QEMU or guest VM execution, including starting or reusing a VM, guest SSH, QGA, reproducing issues inside the guest, or running xfstests or fsstress in the VM. This router chooses the correct VM-side workflow before detailed debugging starts.
---

# Kernel VM Router

Use this router after `kernel-workflow-router` when the task is VM-first.

## Choose The Next Skill

- Use `f2fs-qemu-agent-pipeline` for normal QEMU bring-up, guest readiness checks, SSH or QGA command execution, and routine kernel or filesystem testing inside the guest.
- Use `kernel-debug-orchestrator` when the task is an iterative debug loop: instrument, build, boot, reproduce, collect evidence, refine, and repeat.
- Use `xfstests-qga-ubuntu` when SSH is unavailable and the user specifically needs xfstests management through QGA.
- Use `fsstress-qga-deploy` when the narrow goal is to expose or run `fsstress` inside an already running QGA-controlled guest.

## Handoff Rules

- Start with `f2fs-qemu-agent-pipeline` unless the request is clearly xfstests-only or an established debug loop.
- If the guest must be started or verified before anything else, do that first.
- Once the guest runtime is stable and the next step becomes trace analysis or instrumentation planning, switch to `kernel-analysis-router`.
