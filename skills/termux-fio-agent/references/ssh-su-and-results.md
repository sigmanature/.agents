# SSH plus su and result retrieval

## Recommended privilege model

Use SSH to log into Termux as the normal Termux app user. Use `su -c` only for privileged subcommands.

Good:

```bash
ssh termux-fio-2 'su -c id'
```

Avoid making the entire automation depend on a root login shell. Root shells can have a different home, different PATH, and can create files the Termux user cannot later read.

## First root authorization

The first `su -c` call may block until the human approves root permission on the phone. Use `ssh -tt` if an interactive TTY helps the root manager surface prompts:

```bash
ssh -tt termux-fio-2 'su -c id'
```

If it hangs or fails, ask the human to approve Termux in the root manager.

## PATH differences

Inside `su -c`, Termux paths may not be present. Use absolute paths or set PATH explicitly:

```bash
ssh termux-fio-2 'su -c "PATH=/data/data/com.termux/files/usr/bin:$PATH; command -v fio; id"'
```

## Ownership problems

If root creates result files, SCP as the Termux user may fail. Fix ownership before pulling:

```bash
ssh termux-fio-2 '
  latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
  uid=$(id -u)
  gid=$(id -g)
  su -c "chown -R $uid:$gid $latest"
'
```

## Pulling result files

Yes, host SSH/SCP can pull files from the phone. Prefer tar on device, then SCP one archive:

```bash
ssh termux-fio-2 '
  latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
  tar -C "$(dirname "$latest")" -czf ~/fio-last-result.tgz "$(basename "$latest")"
  echo "$latest"
'
mkdir -p ./fio-results
scp termux-fio-2:~/fio-last-result.tgz ./fio-results/termux-fio-2-last.tgz
tar -xzf ./fio-results/termux-fio-2-last.tgz -C ./fio-results
```

When using the bundled `f2fs_fio_matrix.sh`, preserve the runtime F2FS knobs in result metadata and filenames. At minimum, comparisons should keep these dimensions separate:

- `order` from `max_folio_order_cap`
- `batch_read` from `batch_read_pages_pending`
- `skip_ffs` from `skip_ffs_for_whole_bio`

Do not merge `batch_read=0` and `batch_read=1` runs into the same comparison bucket even if `skip_ffs` is unchanged.
Do not reuse a test file across different `max_folio_order_cap` settings. Delete the old file first, then recreate or refill it under the target order, otherwise the file layout can carry state from the previous configuration and invalidate the comparison.

For aggregate presentation, prefer explicit config labels over nicknames. Good column labels are:

- `order=0,batch=-,skip=-`
- `order=2,batch=0,skip=0`
- `order=2,batch=1,skip=0`
- `order=2,batch=1,skip=1`

Avoid labels such as `baseline`, `Kim`, `batch`, or `full` in the final exported comparison table.

## Avoid adb shell for Termux private paths

Do not rely on host `adb shell` to manipulate `/data/data/com.termux/files/home`. The ADB shell UID, Termux UID, SELinux context, and file ownership can differ. Use SSH/SCP into Termux instead.
