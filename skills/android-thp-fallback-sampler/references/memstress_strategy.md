# Memstress strategy (simplified: `am start` + HOME, no force-stop)

`scripts/run_memstress_and_collect_logs.py` is intentionally kept simple:

- Always uses `am start -n <component>` (**no `-W`**) so it does not fully wait for Activity launch completion.
- After each successful launch:
  - sleep `--hold-ms` (default **200ms**)
  - press HOME (`input keyevent KEYCODE_HOME`) to exit foreground
- **Never uses `am force-stop`**, and does not implement any LRU eviction.

This matches the “flash into app briefly then return HOME, but don’t kill” workload.

## Useful parameter set (extreme churn)

```bash
--burst-size 1 \
--heavy-per-burst 0 \
--hold-ms 200 \
--launch-gap-ms 0 \
--cycle-sleep-ms 0
```

## Verifying behavior

- Per-cycle log: `<out_dir>/<serial>/memstress/cycle_log.jsonl`
  - `launched`: packages successfully started
  - `launch_errors`: launch failures (no force-stop cleanup)
