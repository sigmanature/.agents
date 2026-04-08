# vm_ssh.sh reference

## Path
`.agents/tools/vm_ssh.sh`

## Purpose
Run non-interactive guest commands over SSH using workspace defaults.

## Usage
```bash
bash .agents/tools/vm_ssh.sh '<remote command>'
bash .agents/tools/vm_ssh.sh --instance vm2 '<remote command>'
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

If `--instance` is used, it loads `myscripts/vm_instances/<instance>/instance.env` and uses `VM_SSH_PORT` from that file.

## Caveats
- Password auth may be blocked in some images.
- If SSH is unavailable but QGA is up, switch to `qga_exec.py` per skill policy.
