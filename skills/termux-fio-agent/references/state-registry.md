# State registry

## Purpose

The registry is the source of truth for device alias, ADB serial, host local port, remote Termux sshd port, Termux username, identity file, SSH host key alias, status, and notes.

Use a writable registry at:

```text
${TERMUX_FIO_STATE_DIR:-$PWD/.termux-fio-agent}/devices.tsv
```

The bundled `state/devices.tsv` is a seed. Copy it into the writable registry with `scripts/host/registry.py init` before making changes.

## Schema

```text
alias	serial	local_port	remote_port	termux_user	identity_file	host_key_alias	status	notes
```

Fields:

- `alias`: SSH host alias such as `termux-fio-1`.
- `serial`: ADB serial from `adb devices`; use `UNKNOWN` until confirmed.
- `local_port`: host port used by `adb forward tcp:LOCAL tcp:REMOTE`.
- `remote_port`: Termux sshd port on the Android device, usually `8022`.
- `termux_user`: output of `whoami` inside Termux, such as `u0_a290`.
- `identity_file`: host private key path, usually `~/.ssh/termux_fio`.
- `host_key_alias`: stable SSH host key alias, usually same as `alias`.
- `status`: lifecycle marker, for example `public_key_pushed`, `bootstrap_pending`, `ssh_config_written`, `ssh_verified`, `retired`.
- `notes`: short machine-readable note.

## Port allocation

The remote Termux port can remain `8022` on every Android device. The host local port must be unique per connected device.

Allocate the next local port as:

```text
max(existing local_port) + 1
```

Skip any already used port. With the current seed, ports `8022` and `8023` are used, so the next default is `8024`.

## Update rules

- Initialize the registry before any add/update/list action.
- After pushing a public key, set status to `public_key_pushed`.
- After the human runs bootstrap and returns `USER=...`, update `termux_user` and set status to `bootstrap_done`.
- After writing SSH config, set status to `ssh_config_written`.
- After successful `ssh ALIAS 'id; pwd; command -v fio; command -v python'`, set status to `ssh_verified`.
- Never overwrite an existing alias unless explicitly replacing it.
- Do not reuse a local port unless the old device is retired or the old mapping was removed.
