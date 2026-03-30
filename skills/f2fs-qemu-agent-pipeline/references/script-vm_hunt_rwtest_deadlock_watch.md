# script: `vm_hunt_rwtest_deadlock_watch.sh`

Path: `scripts/vm_hunt_rwtest_deadlock_watch.sh`

Purpose:
- Watch guest tasks and capture full evidence only when `rw_test.py` blocking is sustained.
- Avoid false positive classification from transient `D` state.

Decision model:
1. Poll guest `D` state tasks.
2. Filter only `rw_test.py` entries.
3. Require sustained streak (`STREAK_NEED`) before capture.
4. Capture snapshot #1 (`sysrq w/t/l` + `dmesg` + `ps` + workload log copy).
5. Wait `VERIFY_GAP_SEC`.
6. Re-check sustained blocking and capture snapshot #2.
7. Compare:
   - key stack signature hash from dmesg
   - workload log line count progress
8. Report `DEADLOCK_SUSPECT=1` only when both hold:
   - stack signature stable across snapshots
   - no workload progress

Outputs:
- `/tmp/hunt_dmesg_<ts>.host.txt`
- `/tmp/hunt_ps_<ts>.host.txt`
- `/tmp/hunt_stack_<ts>.md`
- `/tmp/hunt_guest_log_<ts>.host.txt`
- decision and paths in `/tmp/host_hunt_watch.log`

Important:
- `D` state alone is not deadlock.
- `sysrq w` stack dump alone is not deadlock.
- Treat deadlock as: sustained block + stable stack + no progress.
