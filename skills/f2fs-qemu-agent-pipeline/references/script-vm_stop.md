# vm_stop.sh reference

## Path
`.agents/tools/vm_stop.sh`

## Purpose
Stop running `qemu-system-aarch64` safely, with mode-specific delay for crash/deadlock evidence collection.

## Usage
```bash
bash .agents/tools/vm_stop.sh [normal|crash|deadlock]
bash .agents/tools/vm_stop.sh [normal|crash|deadlock] --instance vm2
```

## Modes
- `normal`: stop immediately
- `crash`: wait `VM_STOP_WAIT_SECS` (default 5s), then stop
- `deadlock`: same as crash mode

## Behavior
- With `--instance`, reads `myscripts/vm_instances/<instance>/qemu.pid`.
- Without `--instance`, only proceeds when exactly one `qemu-system-aarch64` exists; otherwise it blocks.
- Sends `TERM`, waits `VM_STOP_GRACE_SECS` (default 3s).
- Escalates to `KILL` if still running.
- Emits status lines including `status=success|failed|blocked`.

## Caveats
- In multi-instance workflows, always pass `--instance` to avoid stopping the wrong VM.
- Always re-check post-stop process list before claiming completion.
