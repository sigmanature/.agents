# Device onboarding workflow

## Host prerequisites

The host must have:

- `adb`
- `ssh`
- `scp`
- host private key `~/.ssh/termux_fio`
- host public key `~/.ssh/termux_fio.pub`

If the key does not exist, create it on the host:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/termux_fio -N ""
```

## Step 1: discover ADB devices

Run:

```bash
adb devices
```

If status is `unauthorized`, ask the human to unlock the phone and approve USB debugging.

If multiple devices are listed and the target is ambiguous, ask the human which serial should be used.

## Step 2: allocate alias and port

Run:

```bash
python3 scripts/host/registry.py suggest --state-dir ./.termux-fio-agent
```

Use the suggested alias and local port unless the user requests another.

## Step 3: push public key

Run:

```bash
adb -s DEVICE_SERIAL push ~/.ssh/termux_fio.pub /sdcard/Download/termux_fio.pub
```

Update registry status to `public_key_pushed`.

## Step 4: human bootstrap inside Termux

Ask the human to open Termux on the target phone and run the bootstrap script. They can paste the script content from `scripts/device/termux_bootstrap.sh`, or if it was copied to Download:

```bash
bash ~/storage/downloads/termux_bootstrap.sh
```

The human must return lines like:

```text
USER=u0_a291
HOME=/data/data/com.termux/files/home
SSHD=12345 sshd -p 8022
AUTHORIZED_KEYS=1
FIO=/data/data/com.termux/files/usr/bin/fio
PYTHON=/data/data/com.termux/files/usr/bin/python
```

## Step 5: update registry

Run:

```bash
python3 scripts/host/registry.py add \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3 \
  --serial DEVICE_SERIAL \
  --local-port 8024 \
  --remote-port 8022 \
  --termux-user u0_a291 \
  --status bootstrap_done
```

## Step 6: write SSH config

Run:

```bash
python3 scripts/host/registry.py write-ssh-config \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3 \
  --ssh-config ~/.ssh/config
```

## Step 7: create ADB forward

Run:

```bash
python3 scripts/host/registry.py adb-forward \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3
```

This creates:

```text
127.0.0.1:LOCAL_PORT -> DEVICE_SERIAL:8022
```

## Step 8: test SSH

Run:

```bash
ssh termux-fio-3 'id; pwd; command -v fio; command -v python'
```

If successful, update status:

```bash
python3 scripts/host/registry.py update \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3 \
  --status ssh_verified
```
