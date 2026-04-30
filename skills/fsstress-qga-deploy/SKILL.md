---
name: fsstress-qga-deploy
description: Use when a user wants to deploy, expose, smoke-test, or run xfstests fsstress inside a QEMU Ubuntu guest through QGA, especially in learn_os workspaces, QGA-only guest control, missing fsstress in PATH, /var/lib/xfstests/ltp/fsstress, or F2FS stress workload setup.
---

# fsstress QGA Deploy

## Overview

Use this skill to make `fsstress` available in an already-running QEMU Ubuntu guest controlled through QGA. The narrow goal is not to reinstall all of xfstests by default: first reuse an existing xfstests build, expose `ltp/fsstress` through `/usr/local/bin/fsstress`, then prove it with a short smoke run.

This skill is designed for `learn_os` style workspaces where `/home/nzzhao/learn_os/.agents/tools/qga_exec.py` is available and the legacy QGA socket is often `/tmp/qga.sock`.

## Files

- `scripts/deploy_fsstress_via_qga.sh`: host-side deployment and smoke wrapper.
- `references/usage.md`: common commands and target-directory guidance.
- `evals/evals.json`: lightweight pressure prompts for future behavioral checks.

## Workflow Contract

### Main Workflow
1. Confirm the target QGA socket with a real handshake.
2. Run `scripts/deploy_fsstress_via_qga.sh`, passing `--sock` when not using `/tmp/qga.sock`.
3. Confirm `/usr/local/bin/fsstress` resolves in the guest.
4. Run a timeout-protected smoke workload in a disposable directory.
5. Report the deployed path, smoke exit code, log path, and next workload command.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | QGA socket is unknown | Inspect the running QEMU command line or `myscripts/vm_instances/<instance>/instance.env` | `qga_exec.py --sock <sock> 'echo qga_ok'` prints `qga_ok` | Stop and ask for the intended instance only if multiple live sockets cannot be disambiguated | block |
| Preflight | SSH helper fails or user requests QGA-only | Use `qga_exec.py`; do not require SSH or `sshpass` | Guest command prints `id`/`uname` | Troubleshoot QGA using `f2fs-qemu-agent-pipeline` | replace |
| Deploy | `/usr/local/bin/fsstress` already exists | Keep it if executable, then smoke-test it | `command -v fsstress` | Relink from xfstests candidates | continue |
| Deploy | `fsstress` exists under `/var/lib/xfstests/ltp` or `/root/xfstests-dev/ltp` but not in PATH | Symlink the best candidate to `/usr/local/bin/fsstress` | `command -v fsstress` returns `/usr/local/bin/fsstress` | Run xfstests installer fallback | branch |
| Deploy | No `fsstress` binary exists | Run the existing `xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh`, then rerun this skill's deploy script | `/var/lib/xfstests/ltp/fsstress` exists | Report missing network/dependency failure from xfstests install logs | branch |
| Verify | `fsstress -H` prints usage but exits nonzero | Treat usage text as help proof; capture rc without `set -e` aborting | Output starts with `Usage: fsstress` | Use direct binary path and recheck | continue |
| Verify | Smoke workload hangs | Wrap smoke with `timeout` and write guest log under `/tmp` | Exit code is not `124` | Reduce `-n`, `-p`, or switch target directory | branch |
| Workload | User wants F2FS-specific stress | Verify/mount the intended F2FS test directory before running `fsstress -d` | `findmnt -T <dir>` shows expected filesystem | Use `/tmp` only for tool smoke; do not claim F2FS coverage | branch |

### Output Contract

- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Commands

Default deploy and smoke against the legacy socket:

```bash
/home/nzzhao/.agents/skills/fsstress-qga-deploy/scripts/deploy_fsstress_via_qga.sh
```

Explicit socket:

```bash
/home/nzzhao/.agents/skills/fsstress-qga-deploy/scripts/deploy_fsstress_via_qga.sh \
  --sock /tmp/qga.sock
```

Use a target directory other than `/tmp/fsstress_smoke`:

```bash
/home/nzzhao/.agents/skills/fsstress-qga-deploy/scripts/deploy_fsstress_via_qga.sh \
  --target-dir /mnt/f2fs/fsstress_smoke \
  --nops 1000 \
  --nproc 4 \
  --timeout 60
```

If deployment reports that xfstests is missing, run the broader installer first:

```bash
/home/nzzhao/.agents/skills/xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh
```

Then rerun this skill's deploy script.

## Notes

- `fsstress` is built by xfstests under `ltp/fsstress`; many installs expose `check` but not `fsstress`.
- `fsstress -H` can return `1` while still printing valid usage. Do not use that exit code alone as failure.
- Use `/tmp` only for tool availability smoke. For filesystem evidence, run `findmnt -T <target>` and report the real target filesystem.

