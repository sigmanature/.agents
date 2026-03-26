# Troubleshooting (QGA-Only, Ubuntu Guest)

Use this section as fallback playbook when automation fails.

## A) `make install` fails with `../install-sh: No such file or directory`

Symptom in log:

```text
/bin/bash: line 1: ../install-sh: No such file or directory
... Error 127
```

Fix:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /root/xfstests-dev; cp -f include/install-sh ./install-sh; chmod +x ./install-sh; make install'
```

Why: some trees keep `install-sh` only under `include/` while install rules in subdirs use `../install-sh`.

## B) `feature.c` mount_attr compile errors

Symptom:

- `struct mount_attr ... incomplete type`
- `MOUNT_ATTR_IDMAP` member errors

Fix:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /root/xfstests-dev; grep -q "^#include <linux/mount.h>$" src/vfs/missing.h || sed -i "/^#include <linux\\/types.h>$/a #include <linux/mount.h>" src/vfs/missing.h'
```

Then rerun configure/build.

## C) XFS header conflicts or redefinitions

Symptom examples:

- `redefinition of struct fsxattr`
- many conflicts from mixed `xfs/*.h`

Root cause:

- previous runs overlaid incompatible upstream xfs headers into `/usr/include/xfs`.

Fix (reset to distro headers):

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'rm -f /usr/include/xfs/*.h || true; DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y xfslibs-dev'
```

Sanity probe:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py "printf '#include <xfs/xfs.h>\n#include <xfs/xqm.h>\n#include <xfs/handle.h>\n' | gcc -x c - -c -o /tmp/_xfs_hdr_test.o"
```

## D) `bc not found` when running check

Fix:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'DEBIAN_FRONTEND=noninteractive apt-get install -y bc'
```

## E) `unknown test` for given case ID

Fix:

- verify with dry run first.

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /var/lib/xfstests && ./check -n ext4/001 ext4/002 ext4/003'
```

Then remove unknown IDs from real run list.

## F) Guest cannot mount XFS (`unknown filesystem type xfs`)

Use ext4/f2fs tests instead of xfs tests in this guest kernel.

Example quick run:

```bash
/home/nzzhao/learn_os/skills/xfstests-qga-ubuntu/scripts/run_ext4_quick_smoke_via_qga.sh
```

## G) Long-running tests may hang

Use per-test timeout and run case-by-case:

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'cd /var/lib/xfstests; timeout 240 ./check ext4/001'
```

Exit code `124` means timeout.

## H) Need clean rerun

```bash
python3 /home/nzzhao/learn_os/.agents/tools/qga_exec.py 'pkill -f install_xfstests || true; pkill -f "/root/xfstests-dev" || true; pkill -f "make install" || true'
```

Then rerun installer script.
