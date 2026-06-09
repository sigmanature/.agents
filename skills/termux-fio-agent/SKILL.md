---
name: termux-fio-agent
description: termux/android device onboarding and operation over adb forward plus ssh/scp for fio testing. use when the user wants chatgpt or an automation agent to add a new termux device, maintain a persistent device-to-port registry, allocate the next local adb forward port, generate ssh config entries, run remote commands, handle required human handoffs for adb/storage/root authorization, combine ssh with su safely, and pull fio results from android termux back to the host.
---

# Termux FIO Agent

## Core rule

Treat the device registry as part of the workflow. Before adding or operating any device, load or initialize the writable registry, then update it after every successful onboarding step.

Bundled seed registry:

- `state/devices.tsv`

Writable runtime registry, preferred:

- `${TERMUX_FIO_STATE_DIR:-$PWD/.termux-fio-agent}/devices.tsv`

Use the bundled seed only to initialize the writable registry. Do not assume an installed skill directory is writable or persistent. When the user asks to update the skill package itself, also update the bundled `state/devices.tsv` seed before packaging.

## Known seed devices

The bundled seed records the devices already discussed in the setup conversation:

- `termux-fio-1`: host local port `8022` -> device remote port `8022`, Termux user `u0_a290`, SSH already verified, serial still unknown.
- `termux-fio-2`: host local port `8023` -> device remote port `8022`, public key pushed, Termux user and serial still to be confirmed.

For the next new device, allocate the next unused host local port after the maximum existing `local_port`. With the seed above, the next default is `8024`.

## Registry commands

Use `scripts/host/registry.py` for state updates.

Initialize writable state from the seed:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py init --state-dir ./.termux-fio-agent
```

List known devices:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py list --state-dir ./.termux-fio-agent
```

Allocate the next port:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py next-port --state-dir ./.termux-fio-agent
```

Add a device after human returns serial and Termux user:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py add \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3 \
  --serial DEVICE_SERIAL \
  --termux-user u0_a291 \
  --status ssh_verified \
  --notes added_by_agent
```

Write or replace SSH config block:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py write-ssh-config \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3 \
  --ssh-config ~/.ssh/config
```

Create ADB forward from registry:

```bash
python3 /path/to/termux-fio-agent/scripts/host/registry.py adb-forward \
  --state-dir ./.termux-fio-agent \
  --alias termux-fio-3
```

## Add a new device workflow

1. Run `adb devices` on the host.
2. If no authorized serial appears, stop and ask the human to unlock the phone and approve USB debugging.
3. Initialize/list the registry and allocate the next local port.
4. Push the host public key to the target device:

```bash
adb -s DEVICE_SERIAL push ~/.ssh/termux_fio.pub /sdcard/Download/termux_fio.pub
```

5. Stop and ask the human to run `scripts/device/termux_bootstrap.sh` inside Termux on the phone. Provide the script contents or a copy command. The human must return the printed `USER=...`, `HOME=...`, `SSHD=...`, and `AUTHORIZED_KEYS=...` lines.
6. Add/update the registry with the returned Termux user, serial, local port, and status.
7. Write the SSH config block.
8. Create ADB forward.
9. Test SSH:

```bash
ssh termux-fio-N 'id; pwd; command -v fio; command -v python'
```

10. If it succeeds, update registry status to `ssh_verified`.

## Human handoff protocol

Pause and ask the human whenever the next step requires a phone tap, phone unlock, or phone-side terminal action. Use direct messages like:

```text
[HUMAN ACTION REQUIRED]
Please unlock the Android device, approve the USB debugging prompt, then send me the new `adb devices` output.
```

Required handoffs:

- ADB says `unauthorized` or no device is listed.
- Termux needs `termux-setup-storage` permission.
- The device needs Termux-side package install/bootstrap commands.
- First root authorization prompt appears in Magisk, KernelSU, APatch, or another root manager.
- Termux was killed, the phone rebooted, or `sshd -p 8022` is no longer running.
- The agent does not know the target serial, Termux username, or selected local port.

See `references/human-handoff.md` for reusable prompt templates.

## Operate Termux from the host

Prefer SSH/SCP over direct `adb shell` for Termux app-private paths.

Run a command:

```bash
ssh termux-fio-2 'pwd; id; ls -la'
```

Push a script:

```bash
scp ./f2fs_fio_matrix.sh termux-fio-2:~/f2fs_fio_matrix.sh
ssh termux-fio-2 'sed -i "s/\r$//" ~/f2fs_fio_matrix.sh'
```

Run a smoke test and print median results:

```bash
ssh termux-fio-2 '
  set -e
  cd ~
  SIZE=256M RUNTIME=10 REPEAT=1 TIME_BASED=0 COOLDOWN=1 bash ~/f2fs_fio_matrix.sh
  latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
  echo "$latest"
  cat "$latest/median.tsv"
'
```

Pull latest results to host:

```bash
ssh termux-fio-2 '
  set -e
  latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
  tar -C "$(dirname "$latest")" -czf ~/fio-last-result.tgz "$(basename "$latest")"
  echo "$latest"
'
mkdir -p ./fio-results
scp termux-fio-2:~/fio-last-result.tgz ./fio-results/termux-fio-2-last.tgz
tar -xzf ./fio-results/termux-fio-2-last.tgz -C ./fio-results
```

The helper `scripts/host/termux_fio_ops.sh` wraps these common operations.

## SSH plus su rule

Use SSH to enter Termux as the normal Termux app user, then use `su -c` only for commands that actually require root. Do not make the whole SSH session root by default.

Safe test:

```bash
ssh -tt termux-fio-2 'su -c id'
```

If a root authorization prompt appears, pause for human approval.

Use absolute Termux paths or explicit PATH inside `su -c`:

```bash
ssh termux-fio-2 'su -c "PATH=/data/data/com.termux/files/usr/bin:$PATH; id; command -v fio"'
```

If root created result files, fix ownership before SCP:

```bash
ssh termux-fio-2 '
  latest=$(ls -td ~/fio-f2fs-test/results-* | head -1)
  uid=$(id -u)
  gid=$(id -g)
  su -c "chown -R $uid:$gid $latest"
'
```

See `references/ssh-su-and-results.md` for details.

## Scripts and references

- `scripts/host/registry.py`: maintain the device registry, allocate ports, generate SSH config, create ADB forwards.
- `scripts/host/termux_fio_ops.sh`: check SSH, run commands, run fio, tar, and pull results.
- `scripts/device/termux_bootstrap.sh`: human-run bootstrap script inside Termux.
- `scripts/device/test_su.sh`: human-run or SSH-run root check script.
- `references/state-registry.md`: registry schema and persistence rules.
- `references/device-onboarding.md`: full onboarding decision tree.
- `references/human-handoff.md`: required human prompts.
- `references/ssh-su-and-results.md`: SSH plus `su` pitfalls and result retrieval patterns.
