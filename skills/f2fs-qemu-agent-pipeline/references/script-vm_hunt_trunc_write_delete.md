# script: `vm_hunt_trunc_write_delete.sh`

Path: `scripts/vm_hunt_trunc_write_delete.sh`

Purpose:
- Run guest-side inline-encryption deadlock hunting workload with the exact churn pattern:
  `truncate(base_size) -> unaligned buffered write -> delete` in a loop.
- Keep writes buffered (`--no-fsync`) to increase overlap with background writeback.
- Tune dirty/writeback sysctls to trigger background writeback earlier.

What it does:
1. Sets aggressive writeback knobs:
   - `vm.dirty_background_bytes=4MB`
   - `vm.dirty_bytes=16MB`
   - `vm.dirty_writeback_centisecs=10`
   - `vm.dirty_expire_centisecs=20`
   - `vm.dirtytime_expire_seconds=5`
2. Spawns `WORKERS=24` parallel writers.
3. Each worker repeatedly:
   - `truncate -s 65537 <file>`
   - `rw_test.py w 65537 64k ... --no-fsync`
   - `rm -f <file>`
4. Logs to `/tmp/hunt/hunt_trunc_write_delete.log` in guest.

Run via QGA example:
```bash
python3 scripts/qga_exec.py 'nohup /bin/bash /tmp/hunt/run_unaligned_trunc_write_delete.sh >/tmp/hunt/nohup_twd.log 2>&1 &'
```

Validation hints:
- Check workload is alive:
  `ps -ef | grep -E "run_unaligned_trunc_write_delete|rw_test.py" | grep -v grep`
- Check `D` tasks quickly:
  `ps -eo pid,stat,comm,args | awk "$2 ~ /^D/ {print}"`
