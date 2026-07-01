# vm_start_bg.sh reference

## Path
`.agents/tools/vm_start_bg.sh`

## Purpose
Start QEMU in background with built-in readiness verification. Blocks until qemu is confirmed ready, or fails with a clear reason.

## Usage
```bash
bash .agents/tools/vm_start_bg.sh
```
Set `SKIP_VERIFY=1` to skip verification (debug only).

## Inputs
- optional positional args: `LAUNCH_LOG PID_FILE CONSOLE_LOG` (rarely needed)
- requires `.vars.sh` with: `BASE`, `SCRIPT`, `IMG_BASE`
- env `SKIP_VERIFY=1` to skip built-in verification

## Verification sequence (all inline, no manual steps needed)
1. Start `qemu_start_ori.sh` via nohup
2. Poll for `qemu-system-aarch64` process (90s timeout, 2s interval)
3. Poll for `/tmp/qga.sock` and `/tmp/qemu-qmp.sock` (90s timeout)
4. QGA handshake with retries (5 attempts, 2s apart)
5. Print final status

## Output
On success:
- `status=ready`
- `qemu_pid=<real pid>`
- `qga_handshake=ok`
- `launch_log=<path>`
- `console_log=<path>`

On failure:
- `status=failed`
- `reason=no_qemu_process|no_qga_socket|qga_handshake_failed`
- `launch_log=<path>` (check this first)

## Important behavior
- The script blocks until verification passes or times out. This is intentional.
- `qemu_pid` is the real `qemu-system-aarch64` PID, not a wrapper.
- Do not run separate `ps`/socket/handshake checks after this script. The script already did them.
