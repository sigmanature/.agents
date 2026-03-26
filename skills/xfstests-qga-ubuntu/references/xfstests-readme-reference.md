# xfstests README Reference

Use upstream docs as source of truth for test semantics, environment variables, and expected workflow.

## Primary upstream repository

- `https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git`

## Common README locations

Depending on checkout state, consult these files in guest:

- `/root/xfstests-dev/README`
- `/root/xfstests-dev/README.md`
- `/var/lib/xfstests/README` (if installed/copied)

Quick inspect from host via QGA:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /root/xfstests-dev && ls -1 README*'
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /root/xfstests-dev && sed -n "1,200p" README'
```

## What to look for in README

- required tools and package assumptions
- `check` usage and options
- environment variable contract (`TEST_DEV`, `TEST_DIR`, `SCRATCH_DEV`, `SCRATCH_MNT`, `FSTYP`)
- known caveats for filesystem-specific groups

## Local skill precedence

When automation behavior in this skill conflicts with README examples:

1. keep README semantics for test behavior
2. keep this skill's QGA-only execution mechanics (transport and safety wrappers)
3. prefer minimal, reversible changes in guest
