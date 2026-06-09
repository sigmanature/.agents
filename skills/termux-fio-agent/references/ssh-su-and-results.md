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

## Avoid adb shell for Termux private paths

Do not rely on host `adb shell` to manipulate `/data/data/com.termux/files/home`. The ADB shell UID, Termux UID, SELinux context, and file ownership can differ. Use SSH/SCP into Termux instead.
