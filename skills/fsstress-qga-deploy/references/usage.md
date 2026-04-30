# fsstress QGA Usage

## Fast path

```bash
/home/nzzhao/.agents/skills/fsstress-qga-deploy/scripts/deploy_fsstress_via_qga.sh \
  --sock /tmp/qga.sock
```

Expected evidence:

- `/usr/local/bin/fsstress` exists in the guest.
- `fsstress -H` prints usage. Exit code `1` is acceptable for this help probe.
- The smoke run returns `smoke_rc=0`.

## Manual guest commands

Handshake:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py --sock /tmp/qga.sock 'echo qga_ok && uname -a'
```

Check deployed path:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py --sock /tmp/qga.sock 'command -v fsstress && ls -l /usr/local/bin/fsstress'
```

Run a short workload:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py --sock /tmp/qga.sock \
  'rm -rf /tmp/fsstress_run; mkdir -p /tmp/fsstress_run; timeout 30s fsstress -d /tmp/fsstress_run -n 1000 -p 4 -l 1 -c -s 1'
```

## F2FS target guidance

Do not treat `/tmp` smoke as F2FS coverage. Before running on a real target, verify the filesystem:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py --sock /tmp/qga.sock 'findmnt -T /mnt/f2fs'
```

Then run with `--target-dir /mnt/f2fs/fsstress_smoke` or a workload-specific directory.

## Missing fsstress fallback

If the deploy script exits `3`, install xfstests through the existing broader skill:

```bash
/home/nzzhao/.agents/skills/xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh
```

Then rerun the fsstress deploy script.

