#!/usr/bin/env bash
set -euo pipefail

# adb_dexopt_regen_loop.sh
#
# Deterministic loop to force repeated dexopt artifact regeneration (odex+vdex) without reboot.
# Intended for large apps (Douyin/Huoshan/etc.) where you want a repeatable:
#   delete-dexopt -> (optional profile ops) -> compile -> (optional profile dumps)
#
# Requires: host has `adb`, device has USB debugging enabled.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TOOL_PM_ART_DUMP_SUMMARY="$SCRIPT_DIR/pm_art_dump_summary.py"

SERIAL=""
PKG=""
ITERS=5
FILTERS="speed-profile"
REASON="cmdline"
SCOPE="--full"

DO_DELETE_DEXOPT=1
DO_CLEAR_PROFILES=0
DO_FORCE_MERGE_PROFILE=0
DO_SNAPSHOT_PROFILE=0
DO_DUMP_PROFILES=0
PULL_PROFMAN=0

OUTDIR=""

usage() {
  cat <<'EOF'
adb_dexopt_regen_loop.sh: loop forcing repeated dexopt/oat(vdex) regeneration

Usage:
  adb_dexopt_regen_loop.sh --package <pkg.name> [options]

Options:
  -s, --serial <serial>     Target device serial
  -p, --package <pkg>       Package name (required)
  -n, --iters <N>           Iterations (default: 5)
  --filters <csv>           Compiler filters to cycle (default: speed-profile)
  --reason <reason>         Compilation reason for 'pm compile -r' (default: cmdline)
  --scope <flag>            One of: --full | --primary-dex | --secondary-dex (default: --full)
  --no-delete-dexopt        Skip 'pm delete-dexopt' pre-step
  --clear-profiles          Run 'pm art clear-app-profiles' each iteration (keeps external/cloud)
  --force-merge-profile     Add '--force-merge-profile' to compile
  --snapshot-profile        Run 'pm snapshot-profile' after compile (writes under /data/misc/profman)
  --dump-profiles           Run 'pm dump-profiles' after compile (writes under /data/misc/profman)
  --pull-profman            If root is available, tar+pull /data/misc/profman after each iter
  -o, --outdir <dir>        Output directory (default: ./dexopt_loop_<ts>_<pkg>)
  -h, --help                Show help

Notes:
  - Uses 'pm compile -f -m <filter>' to force recompilation even if up-to-date.
  - Stable repro default is the historically proven `speed-profile` path; multi-filter loops are explicit opt-in via `--filters`.
  - Pulling /data/misc/profman typically needs root (userdebug: adb root; retail: Magisk su).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    -p|--package) PKG="${2:-}"; shift 2;;
    -n|--iters) ITERS="${2:-}"; shift 2;;
    --filters) FILTERS="${2:-}"; shift 2;;
    --reason) REASON="${2:-}"; shift 2;;
    --scope) SCOPE="${2:-}"; shift 2;;
    --no-delete-dexopt) DO_DELETE_DEXOPT=0; shift;;
    --clear-profiles) DO_CLEAR_PROFILES=1; shift;;
    --force-merge-profile) DO_FORCE_MERGE_PROFILE=1; shift;;
    --snapshot-profile) DO_SNAPSHOT_PROFILE=1; shift;;
    --dump-profiles) DO_DUMP_PROFILES=1; shift;;
    --pull-profman) PULL_PROFMAN=1; shift;;
    -o|--outdir) OUTDIR="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$PKG" ]]; then
  echo "Error: --package is required" >&2
  usage
  exit 2
fi

case "$SCOPE" in
  --full|--primary-dex|--secondary-dex) ;;
  *)
    echo "Error: --scope must be one of --full/--primary-dex/--secondary-dex (got: $SCOPE)" >&2
    exit 2
    ;;
esac

TS="$(date +%Y%m%d_%H%M%S)"
SAFE_PKG="${PKG//[^a-zA-Z0-9._-]/_}"
OUTDIR="${OUTDIR:-./dexopt_loop_${TS}_${SAFE_PKG}}"
mkdir -p "$OUTDIR"

ADB=(adb)
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )

adb_sh() { "${ADB[@]}" shell "$@"; }

summarize_art_dump_file() {
  local input_path="$1"
  local output_path="$2"
  if [[ -s "$input_path" ]] && [[ -f "$TOOL_PM_ART_DUMP_SUMMARY" ]]; then
    python3 "$TOOL_PM_ART_DUMP_SUMMARY" "$input_path" > "$output_path" 2>/dev/null || true
  fi
}

append_effective_summary() {
  local summary_path="$1"
  local log_path="$2"
  local requested_filter="$3"
  if [[ ! -s "$summary_path" ]]; then
    return 0
  fi
  python3 - "$summary_path" "$requested_filter" >> "$log_path" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
requested = sys.argv[2]
entry = next((item for item in summary.get("entries", []) if item.get("abi") == "arm64"), None)
if entry is None and summary.get("entries"):
    entry = summary["entries"][0]
if entry is None:
    sys.exit(0)
print(f"requested_filter={requested or '<unset>'}")
print(f"effective_filter={entry.get('status') or '<unknown>'}")
print(f"effective_reason={entry.get('reason') or '<unknown>'}")
print(f"effective_location={entry.get('location') or '<unknown>'}")
PY
}

has_su() {
  "${ADB[@]}" shell sh -c 'command -v su >/dev/null 2>&1 && su -c id >/dev/null 2>&1' \
    >/dev/null 2>&1
}

pull_profman_if_possible() {
  local iter="$1"

  if [[ "$PULL_PROFMAN" -ne 1 ]]; then
    return 0
  fi

  if ! has_su; then
    echo "[$iter] NOTE: --pull-profman requested but no usable su; skipping pull." | tee -a "$OUTDIR/notes.txt"
    return 0
  fi

  local dev_tar="/data/local/tmp/profman_${SAFE_PKG}_${TS}_iter${iter}.tgz"
  echo "[$iter] Pulling /data/misc/profman via root tar -> $dev_tar"
  "${ADB[@]}" shell su -c "sh -c 'tar -czf \"$dev_tar\" /data/misc/profman 2>/dev/null || true; chmod 0644 \"$dev_tar\"'"
  "${ADB[@]}" pull "$dev_tar" "$OUTDIR/$(basename "$dev_tar")" >/dev/null
  "${ADB[@]}" shell rm -f "$dev_tar" >/dev/null 2>&1 || true
}

echo "Output: $OUTDIR"
echo "Device serial: ${SERIAL:-<default>}" | tee "$OUTDIR/meta.txt"
echo "Package: $PKG" | tee -a "$OUTDIR/meta.txt"
echo "Iters: $ITERS" | tee -a "$OUTDIR/meta.txt"
echo "Filters: $FILTERS" | tee -a "$OUTDIR/meta.txt"
echo "Reason: $REASON" | tee -a "$OUTDIR/meta.txt"
echo "Scope: $SCOPE" | tee -a "$OUTDIR/meta.txt"

echo "pm path:" | tee -a "$OUTDIR/meta.txt"
adb_sh pm path "$PKG" | tr -d '\r' | tee -a "$OUTDIR/meta.txt" || true

IFS=',' read -r -a FILTER_ARR <<<"$FILTERS"
if [[ "${#FILTER_ARR[@]}" -eq 0 ]]; then
  echo "Error: empty --filters" >&2
  exit 2
fi

for ((i=1; i<=ITERS; i++)); do
  filter="${FILTER_ARR[$(( (i-1) % ${#FILTER_ARR[@]} ))]}"
  echo "==== Iteration $i/$ITERS (filter=$filter) ====" | tee -a "$OUTDIR/notes.txt"

  adb_sh pm art dump "$PKG" | tr -d '\r' > "$OUTDIR/iter${i}_art_dump_before.txt" || true
  summarize_art_dump_file "$OUTDIR/iter${i}_art_dump_before.txt" "$OUTDIR/iter${i}_art_dump_before_summary.json"

  if [[ "$DO_DELETE_DEXOPT" -eq 1 ]]; then
    adb_sh pm delete-dexopt "$PKG" | tr -d '\r' | tee "$OUTDIR/iter${i}_delete_dexopt.txt" || true
  fi

  if [[ "$DO_CLEAR_PROFILES" -eq 1 ]]; then
    adb_sh pm art clear-app-profiles "$PKG" | tr -d '\r' | tee "$OUTDIR/iter${i}_clear_profiles.txt" || true
  fi

  compile_args=(pm compile "$SCOPE" -r "$REASON" -f -m "$filter")
  if [[ "$DO_FORCE_MERGE_PROFILE" -eq 1 ]]; then
    compile_args+=( --force-merge-profile )
  fi
  compile_args+=( "$PKG" )

  echo "+ ${compile_args[*]}" | tee "$OUTDIR/iter${i}_compile_cmd.txt"
  adb_sh "${compile_args[@]}" | tr -d '\r' > "$OUTDIR/iter${i}_compile_out.txt" || true

  if [[ "$DO_SNAPSHOT_PROFILE" -eq 1 ]]; then
    adb_sh pm snapshot-profile "$PKG" | tr -d '\r' > "$OUTDIR/iter${i}_snapshot_profile.txt" || true
  fi
  if [[ "$DO_DUMP_PROFILES" -eq 1 ]]; then
    adb_sh pm dump-profiles "$PKG" | tr -d '\r' > "$OUTDIR/iter${i}_dump_profiles.txt" || true
  fi

  adb_sh pm art dump "$PKG" | tr -d '\r' > "$OUTDIR/iter${i}_art_dump_after.txt" || true
  summarize_art_dump_file "$OUTDIR/iter${i}_art_dump_after.txt" "$OUTDIR/iter${i}_art_dump_after_summary.json"
  append_effective_summary "$OUTDIR/iter${i}_art_dump_after_summary.json" "$OUTDIR/notes.txt" "$filter"

  pull_profman_if_possible "$i"
done

echo "Done: $OUTDIR"
