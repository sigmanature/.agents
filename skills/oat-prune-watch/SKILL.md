# oat-prune-watch: Android OAT/VDEX/ART Artifact Pruning Daemon

## Purpose

Continuously delete compiled runtime artifacts (.odex/.vdex/.art/.oat) of target
Android packages, forcing ART recompilation on each app launch. Generates
additional memory pressure from zygote + compiler allocations.

## When to Use

- Need to amplify per-launch memory cost during THP/memory experiments
- Want to force ART AOT/JIT compilation overhead on every app start
- Testing memory behavior under extreme compilation churn

## Usage

```bash
# Prune a specific set of packages
python3 scripts/oat_prune.py \
  --serial 18281FDF6007HB \
  --packages com.tencent.mm com.tencent.tmgp.sgame \
  --poll-s 2.0 \
  --out-dir /tmp/oat_watch \
  --use-su

# Prune all installed 3rd-party packages
python3 scripts/oat_prune.py \
  --serial 18281FDF6007HB \
  --all-packages \
  --poll-s 5.0 \
  --out-dir /tmp/oat_watch
```

## Output

```
<out_dir>/
├── oat_watch.jsonl       # per-poll results: deleted count, paths
└── oat_watch_status.json # latest poll status (overwritten each cycle)
```

## How It Works

1. Gets APK path via `pm path <pkg>`
2. Deletes matching files in `<apk_dir>/oat/` and `/data/dalvik-cache/`
3. ART detects missing artifacts → recompiles on next launch
4. Repeats every `--poll-s` seconds until killed