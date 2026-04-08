# vm_start_bg.sh reference

## Path
`.agents/tools/vm_start_bg.sh`

## Purpose
Start QEMU launcher in background and emit launcher metadata (pid/log paths).

## Inputs
- arg1: `LAUNCH_LOG` (optional, default `.roo/plans/qemu-launch.log`)
- arg2: `PID_FILE` (optional, default `.roo/plans/qemu.pid`)
- arg3: `CONSOLE_LOG` (optional, default `guest_console.log`)
- requires `.vars.sh` with: `BASE`, `SCRIPT`, `IMG_BASE`
- instance mode (preferred for parallel):
  - `--launcher ubuntu-cow --instance vm2`
  - optional: `--launch-log <path> --pid-file <path> --console-log <path>`

## Output
Prints key-value lines:
- `pid=<wrapper_pid>`
- `launch_log=<path>`
- `console_log=<path>`
- `launcher=<ori|ubuntu-cow>`
- when `--instance` is used: `instance=<name>` and `instance_env=<path>`

## Important behavior
- `pid` is the `nohup` wrapper PID, not guaranteed to be long-lived `qemu-system-aarch64`.
- Must follow with explicit post-check:
  - process: `ps ... qemu-system-aarch64`
  - sockets: legacy `/tmp/qga.sock` / multi-instance `VM_QGA_SOCK` in `instance.env`
  - handshake: `python3 .agents/tools/qga_exec.py --sock <qga_sock> 'echo qga_ok'`

## Common failures
- wrapper exits quickly without useful logs
- background starts but `qga_exec.py` fails due to missing socket
- socket exists but `ConnectionRefusedError`

Use troubleshooting playbook:
[`qga-startup-troubleshooting.md`](qga-startup-troubleshooting.md)
