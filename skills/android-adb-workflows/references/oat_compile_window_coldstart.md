# Fixed Compile-Window Cold-Start Workflow

Use this when the goal is **not** just “run dexopt and later cold-start the app”, but specifically:

- force a fresh oat/vdex rewrite every iteration
- launch the app **during** the `dex2oat` compile window
- prove that the launch overlapped a live rewrite window via `.tmp` watcher evidence
- collect `logcat`, `dmesg`, and `tracefs` together

## Non-negotiable invariant per iteration

For every cold-start attempt, keep this order:

1. `pm delete-dexopt <pkg>`
2. `pm compile ... <pkg>` starts
3. app cold-start attempts happen while compile is still running

Do **not** treat “repeated launches” alone as sufficient. If you skip `delete-dexopt`, the app can
land on already-existing oat/vdex artifacts and you lose the guarantee that startup is consuming a
fresh rewrite window.

## Why this matters for tiny controllable apps

Small apps compile fast. That creates two risks:

- compile can finish before your launch lands
- old artifacts can survive and make a later launch look like a “compile-window” run when it was
  actually a consume-from-existing-oat run

So the fixed baseline is:

- `reason=cmdline`
- `filter=speed-profile`
- `delete-dexopt` before each compile window
- force-stop driven cold starts, not warm relaunches

## Evidence priority

Preferred proof of a live rewrite window:

1. `.tmp` watcher hits under the current package install dir
2. `logcat` evidence such as `artd` opening `*.tmp`
3. tracefs + timeline alignment showing compile/start overlap

If `.tmp` watcher and `artd` evidence disagree, trust the watcher first.

## Watcher rule

Do **not** watch a frozen `/data/app/.../pkg-random==` path across reinstall/update cycles.

`adb install -r` can move the package to a new randomized install directory. The watcher should
resolve the current package path from `pm path <pkg>` each poll, then derive the active package dir.

Use:

```bash
chmod +x scripts/adb_oat_tmp_watcher.sh
./scripts/adb_oat_tmp_watcher.sh \
  --serial <SERIAL> \
  --package <pkg.name> \
  --output <run_dir>/tmp_watcher.txt
```

Add `--include-final` when you also want continuous `*.odex` / `*.vdex` visibility.

## Fixed baseline command family

Main capture:

```bash
chmod +x scripts/adb_oat_rewrite_capture.sh
./scripts/adb_oat_rewrite_capture.sh \
  --serial <SERIAL> \
  --package <pkg.name> \
  --apk <path.apk> \
  --iters 6 \
  --filters speed-profile \
  --reason cmdline \
  --delete-dexopt \
  --tracefs \
  --post-start-open-window-sec 2 \
  --launch-interval 1 \
  --foreground-hold 1 \
  --post-cold-start-delays 0,1,3,5
```

Parallel kernel log stream:

```bash
adb -s <SERIAL> exec-out su -c 'dmesg -wT' > <run_dir>/dmesg_live.txt
```

This keeps the experiment aligned to the stable baseline while still increasing overlap odds for a
small app.

## Acceptance checklist

- `compile_cmd.txt` shows `pm compile ... -f -m speed-profile`
- `delete_dexopt.txt` exists for the iteration
- `launch_loop.txt` shows cold-start attempts during compile
- `tmp_watcher.txt` contains `*.tmp` during the same iteration window
- `logcat_all_threadtime.txt` contains app startup markers for that window
- `trace_pipe.txt` / `trace_snapshot.txt` exist when `--tracefs` is enabled
- `dmesg_live.txt` is present for the same run directory
