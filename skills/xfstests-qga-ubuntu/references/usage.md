# Usage Guide

## 1) Full install in guest (QGA-only)

Run from host:

```bash
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh
```

Optional parameters:

```bash
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh \
  --qga-exec /home/nzzhao/learn_os/.agents/tools/qga_exec.py \
  --timeout 14400
```

Post-check:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py '/usr/local/bin/check -h | head -n 20'
```

## 2) Quick ext4 smoke (hang-safe)

This creates isolated loopback images and runs a small ext4 test set with per-case timeout.

```bash
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/run_ext4_quick_smoke_via_qga.sh
```

Custom test list and timeout:

```bash
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/run_ext4_quick_smoke_via_qga.sh \
  --tests 'ext4/001 ext4/003' \
  --per-test-timeout 240
```

## 3) Run manual tests after setup

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /var/lib/xfstests && ./check -n generic/001'
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /var/lib/xfstests && ./check generic/001'
```

## 4) Logs and evidence locations (guest)

- Full install log: `/tmp/xfstests_install_full.log`
- Retry install log: `/tmp/xfstests_make_install_retry.log`
- Smoke logs: `/tmp/ext4_quick_summary.txt`, `/tmp/ext4_quick_*.out`, `/tmp/ext4_quick_*.err`
- Installed tree: `/var/lib/xfstests`
- Wrapper command: `/usr/local/bin/check`
