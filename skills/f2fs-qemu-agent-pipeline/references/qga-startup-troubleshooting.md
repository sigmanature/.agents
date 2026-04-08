# QGA startup troubleshooting

This playbook captures recurring failures observed while starting QEMU for QGA-driven workflows.

## Required verification sequence
Run in this exact order after start:

1. Process check:
```bash
ps -ef | grep -E 'qemu-system-aarch64|qemu_start_ori.sh|qemu_start_ubuntu.sh' | grep -v grep
```

2. Socket check:
```bash
ls -l /tmp/qga.sock /tmp/qemu-qmp.sock
```

For multi-instance mode, prefer reading socket paths from:
`myscripts/vm_instances/<instance>/instance.env` (`VM_QGA_SOCK`, `VM_QMP_SOCK`).

3. Handshake check:
```bash
python3 .agents/tools/qga_exec.py --timeout 15 --sock /tmp/qga.sock 'echo qga_ok && uname -a'
```

Only when all three pass, treat VM as ready.

## Symptom: wrapper says started, but no qemu process

- Typical signal:
  - `vm_start_bg.sh` prints pid/log paths
  - `ps` shows no `qemu-system-aarch64`
  - launch log only has config header
- Action:
  - do not claim success
  - rerun in foreground diagnosis mode:
```bash
timeout 15 bash myscripts/qemu_start_ori.sh --log /tmp/qemu_probe.log
```
  - capture full stderr/stdout reason

## Symptom: host-side `nohup ... < /dev/null &` backgrounding exits quickly

- Typical signal:
  - launcher returns quickly
  - console log stays empty
  - `/tmp/qga.sock` or `/tmp/qemu-qmp.sock` may appear briefly and then disappear
  - `ps` no longer shows the real `qemu-system-aarch64`
- Cause:
  - backgrounding a foreground stdio-based launcher purely from the host shell is fragile because the VM still depends on a tty-shaped console backend
- Action:
  - rerun the launcher in a real PTY for diagnosis
  - if you need a detached boot path, use a launcher that was designed for non-stdio startup rather than `nohup` around the interactive foreground path

## Symptom: socket exists but QGA `ConnectionRefusedError`

- Means stale socket path or no active listener.
- Action:
  - verify real process tree
  - remove stale socket only when no active qemu owns it
  - relaunch and re-run handshake

## Symptom: QGA `FileNotFoundError` on `/tmp/qga.sock`

- Typical signal:
  - background launcher printed only the config header
  - `ps` shows no `qemu-system-aarch64`
  - `python3 .agents/tools/qga_exec.py ...` fails with `FileNotFoundError: [Errno 2] No such file or directory`
- Action:
  - classify it as the same false-positive startup family
  - do not trust wrapper exit status by itself
  - re-check the real QEMU process first, then relaunch in foreground diagnosis mode if needed

## Symptom: bind failure in restricted environment

- Error:
  - `Failed to bind socket to /tmp/qga.sock: Operation not permitted`
- Action:
  - start VM outside sandbox restrictions
  - re-check sockets and handshake

## Symptom: foreground boot works but background launch appears flaky

- Action:
  - keep one foreground PTY session for stabilization by using `myscripts/qemu_start_ori.sh`
  - run guest setup, dynamic-debug enablement, and test orchestration through QGA in parallel host shells
  - once root cause identified, return to background mode

## Reporting template

```text
command/script: <start/check command>
status: success | failed | blocked
log: <launcher log / console log path>
next step: <exact command>
```
