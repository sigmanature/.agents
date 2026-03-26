---
name: xfstests-qga-ubuntu
description: Install, repair, and smoke-test xfstests inside a QEMU Ubuntu guest when SSH is unavailable and all operations must run through qga_exec.py. Use this skill whenever the user mentions xfstests installation failures, missing /usr/local/bin/check, QGA-only guest control, loopback-based test setup, or asks for reproducible xfstests automation and troubleshooting playbooks.
---

# xfstests QGA Ubuntu Skill

Use this skill when you must manage `xfstests` in a QEMU Ubuntu guest via QGA only.

## What this skill provides

- One-click host-side automation to install xfstests in guest via `qga_exec.py`.
- Defensive handling for common failures seen in QGA-only flows.
- Quick ext4 smoke workflow that avoids long hangs.
- A compact troubleshooting runbook and usage guide.
- Upstream README reference pointers.

## Preconditions

- Host has access to guest QGA socket and runner script:
  - `/home/nzzhao/learn_os/.agents/tools/qga_exec.py`
- Guest has network for apt/git.
- Guest is Ubuntu-like environment with `apt-get`.

## Files in this skill

- `scripts/install_xfstests_via_qga.sh`
  - Full guest install automation for xfstests.
- `scripts/run_ext4_quick_smoke_via_qga.sh`
  - Loopback-based ext4 quick smoke tests with per-test timeout.
- `references/usage.md`
  - How to run install, smoke, and routine commands.
- `references/troubleshooting.md`
  - Incident handling and fallback commands.
- `references/xfstests-readme-reference.md`
  - Upstream README references and how to consult them.

## Fast path workflow

1. Run full install script from host:
   - `scripts/install_xfstests_via_qga.sh`
2. Validate command availability:
   - `python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py '/usr/local/bin/check -h | head -n 20'`
3. Run quick smoke (ext4, loopback, timeout-protected):
   - `scripts/run_ext4_quick_smoke_via_qga.sh`

## Expected success criteria

- `/usr/local/bin/check` exists in guest.
- `check -h` prints usage text.
- At least one real smoke case passes (for example `generic/001` or quick ext4 set).

## When failures occur

Follow `references/troubleshooting.md` in order. It already covers the most common failure classes:

- missing `install-sh` during `make install`
- `mount_attr` compile failure in `feature.c`
- mixed/dirty XFS headers causing compile conflicts
- missing runtime tools like `bc`
- unknown test IDs and timeout strategy

## Reporting template

Use this exact structure when returning results:

- `command/script`: what was run
- `status`: success | failed | blocked
- `evidence`: key output and file paths
- `next step`: smallest safe follow-up
