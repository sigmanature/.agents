# Running long THP+memstress jobs detached (Codex / non-interactive shells)

When launching a multi-hour run, you often want the process to survive after the terminal command returns.

In some managed exec environments (including Codex tool runs), doing `nohup ... &` from a one-shot command may still get cleaned up when the parent command finishes (process group cleanup). A more reliable pattern is to spawn a new session via `setsid` and write a pidfile from inside the detached shell.

## Recommended pattern (setsid + pidfile)

If you prefer a packaged helper instead of a one-liner, use:

```bash
chmod +x /home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/launch_memstress_detached.sh
/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/launch_memstress_detached.sh --help
```

### Example: UC Browser + Douyin + Huoshan (3-app loop)

Workload: repeatedly `am start` each app, short dwell, then press HOME (no force-stop).

Packages (as of 2026-04-16 on a Pixel 6 test device):

- UC Browser: `com.UCMobile`
- Douyin: `com.ss.android.ugc.aweme`
- Huoshan (Douyin Huoshan): `com.ss.android.ugc.live`

```bash
chmod +x /home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/launch_memstress_uc_douyin_huoshan_detached.sh

/home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/launch_memstress_uc_douyin_huoshan_detached.sh \
  --repo /home/nzzhao/learn_os/output/top100_install_20260325_dual \
  --serial 21121FDF600C4G \
  -- --hold-ms 1500 --burst-size 1 --cycle-sleep-ms 200
```

```bash
REPO=/path/to/top100_install_...   # contains all_packages.txt
TS=$(date +%Y%m%d_%H%M%S)
OUTDIR="$REPO/output/thp_memstress_dual_${TS}"
mkdir -p "$OUTDIR"

# Use a single-quoted `bash -lc` script so the *inner* shell expands `$$`,
# while `$REPO/$OUTDIR` are provided via env vars.
export REPO OUTDIR
setsid -f bash -lc 'cd "$REPO"; echo $$ > "$OUTDIR/host_pid.txt"; \
  exec env PYTHONUNBUFFERED=1 python3 /home/nzzhao/.agents/skills/android-thp-fallback-sampler/scripts/run_memstress_and_collect_logs.py \
    --serial SERIAL_A --serial SERIAL_B --jobs 2 \
    --out-dir "$OUTDIR" \
    --duration-s 57600 --interval-s 60 \
    --package-file ./all_packages.txt \
    >"$OUTDIR/host_stdout.txt" 2>"$OUTDIR/host_stderr.txt"'

echo "launched pid=$(cat "$OUTDIR/host_pid.txt") outdir=$OUTDIR"
```

## Pitfall: don't trust `$!` with `setsid -f ... &`

Avoid patterns like:

```bash
setsid -f python3 ... >out.log 2>&1 < /dev/null &
echo $! > out.pid   # often NOT the python pid
```

`setsid -f` may fork. The PID you get from `$!` can be the short-lived parent, not the long-running python process.

Fix: write the pidfile from inside the detached shell (the recommended pattern above), or locate the PID with:

```bash
ps -ef | rg 'run_memstress_and_collect_logs.py'
```

## Monitoring

```bash
OUTDIR=.../output/thp_memstress_dual_<ts>
tail -f "$OUTDIR/host_stdout.txt"
tail -f "$OUTDIR/host_stderr.txt"

# per-device artifacts
tail -f "$OUTDIR/<SERIAL>/memstress/cycle_log.jsonl"
watch -n 10 'wc -l '"$OUTDIR"'/*/raw_samples.csv'
```

## Stopping

```bash
OUTDIR=.../output/thp_memstress_dual_<ts>
kill -INT "$(cat "$OUTDIR/host_pid.txt")"
```
