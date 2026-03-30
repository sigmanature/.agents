# vm_stop.sh reference

## Path
`scripts/vm_stop.sh`

## Purpose
Stop running `qemu-system-aarch64` safely, with mode-specific delay for crash/deadlock evidence collection.

## Usage
```bash
bash scripts/vm_stop.sh [normal|crash|deadlock]
```

## Modes
- `normal`: stop immediately
- `crash`: wait `VM_STOP_WAIT_SECS` (default 5s), then stop
- `deadlock`: same as crash mode

## Behavior
- Finds first `qemu-system-aarch64` PID via `ps`.
- Sends `TERM`, waits `VM_STOP_GRACE_SECS` (default 3s).
- Escalates to `KILL` if still running.
- Emits status lines including `status=success|failed|blocked`.

## Caveats
- If multiple QEMU instances exist, it targets the first matched PID.
- Always re-check post-stop process list before claiming completion.
