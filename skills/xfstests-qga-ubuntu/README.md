# xfstests-qga-ubuntu skill package

This package captures a complete QGA-only workflow for installing and using xfstests in a Ubuntu guest VM.

## Package structure

- `SKILL.md`
- `scripts/install_xfstests_via_qga.sh`
- `scripts/run_ext4_quick_smoke_via_qga.sh`
- `references/usage.md`
- `references/troubleshooting.md`
- `references/xfstests-readme-reference.md`

## Quick start

```bash
# 1) install xfstests in guest via QGA only
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh

# 2) run fast ext4 smoke tests (loopback isolated)
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/run_ext4_quick_smoke_via_qga.sh
```

## Notes

- This workflow intentionally avoids SSH.
- For environments without XFS kernel mount support, smoke defaults to ext4.
