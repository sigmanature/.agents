# qga_exec.py reference

Location:

- `scripts/qga_exec.py`

Purpose:

- Run one host-triggered command inside the current QEMU guest through the QEMU Guest Agent socket.
- Prefer this wrapper over ad-hoc `socat` or handwritten QGA JSON when the task is simply "execute a guest command".

Usage:

```bash
python3 scripts/qga_exec.py 'echo qga_ok && uname -a'
python3 scripts/qga_exec.py --timeout 120 --poll 0.2 'bash /tmp/test.sh'
python3 scripts/qga_exec.py --no-capture 'python3 /root/run_long_test.py > /tmp/test.log 2>&1'
```

Behavior:

- Connects to `/tmp/qga.sock`.
- Runs the guest command through `/bin/bash -lc`.
- Polls `guest-exec-status` until exit or timeout.
- Prints captured stdout/stderr unless `--no-capture` is set.

Operational notes:

- For long-running or noisy tests, redirect inside the guest to a guest-local log file and then fetch the tail in a separate command.
- Treat socket-exists-but-connect-fails as a startup/permission problem, not proof that the VM is healthy.
