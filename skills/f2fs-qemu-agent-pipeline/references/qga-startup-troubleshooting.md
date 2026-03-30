# QGA startup troubleshooting

This playbook captures recurring failures observed while starting QEMU for QGA-driven workflows.

## Required verification sequence
Run in this exact order after start:

1. Process check:
```bash
ps -ef | grep -E 'qemu-system-aarch64|qemu_start_ori.sh' | grep -v grep
```

2. Socket check:
```bash
ls -l /tmp/qga.sock /tmp/qemu-qmp.sock
```

3. Handshake check:
```bash
python3 .agents/tools/qga_exec.py --timeout 15 'echo qga_ok && uname -a'
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

## Symptom: socket exists but QGA `ConnectionRefusedError`

- Means stale socket path or no active listener.
- Action:
  - verify real process tree
  - remove stale socket only when no active qemu owns it
  - relaunch and re-run handshake

## Symptom: bind failure in restricted environment

- Error:
  - `Failed to bind socket to /tmp/qga.sock: Operation not permitted`
- Action:
  - start VM outside sandbox restrictions
  - re-check sockets and handshake

## Symptom: foreground boot works but background launch appears flaky

- Action:
  - keep one foreground PTY session for stabilization
  - run QGA checks and test orchestration in parallel host shells
  - once root cause identified, return to background mode

## Reporting template

```text
command/script: <start/check command>
status: success | failed | blocked
log: <launcher log / console log path>
next step: <exact command>
```
