# qga_exec.py reference

Location:

- primary entrypoint in workspace: `.agents/tools/qga_exec.py`
- user-level reference copy: `scripts/qga_exec.py`

Purpose:

- Run one host-triggered command inside the current QEMU guest through the QEMU Guest Agent socket.
- Prefer this wrapper over ad-hoc `socat` or handwritten QGA JSON when the task is simply "execute a guest command".

Usage:

```bash
python3 .agents/tools/qga_exec.py 'echo qga_ok && uname -a'
python3 .agents/tools/qga_exec.py --timeout 120 --poll 0.2 'bash /tmp/test.sh'
python3 .agents/tools/qga_exec.py --no-capture 'python3 /root/run_long_test.py > /tmp/test.log 2>&1'
python3 .agents/tools/qga_exec.py --sock /tmp/qga.vm2.sock 'echo hello_from_vm2'
```

Behavior:

- Connects to `--sock` if provided, otherwise `QGA_SOCK` env var, otherwise `/tmp/qga.sock`.
- Runs the guest command through `/bin/bash -lc`.
- Polls `guest-exec-status` until exit or timeout.
- Prints captured stdout/stderr unless `--no-capture` is set.

Operational notes:

- For long-running or noisy tests, redirect inside the guest to a guest-local log file and then fetch the tail in a separate command.
- Treat socket-exists-but-connect-fails as a startup/permission problem, not proof that the VM is healthy.
- If the wrapper times out, it exits `124` and prints a hint; the guest command may still be running.
