# vm_ssh.sh reference

## Path
`scripts/vm_ssh.sh`

## Purpose
Run non-interactive guest commands over SSH using workspace defaults.

## Usage
```bash
bash scripts/vm_ssh.sh '<remote command>'
```

## Inputs
- env defaults:
  - `VM_SSH_HOST` (default `127.0.0.1`)
  - `VM_SSH_PORT` (default `5022`)
  - `VM_SSH_USER` (default `root`)
  - `VM_SSH_PASSWORD` (default `1`)
- requires `sshpass` installed

## Behavior
- Uses `sshpass` with password auth.
- Disables strict host key checking for automation convenience.
- `ConnectTimeout=5`.

## Caveats
- Password auth may be blocked in some images.
- If SSH is unavailable but QGA is up, switch to `qga_exec.py` per skill policy.
