#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

SERIAL=""
PKG=""
OUTDIR=""
declare -a PROBE_CLASSES=()

usage() {
  cat <<'EOF'
adb_cnfe_odex_vdex_triage.sh: triage live Android odex/vdex artifacts for CNFE/NCDFE

Usage:
  adb_cnfe_odex_vdex_triage.sh --package <pkg.name> --class <fqcn> [--class <fqcn> ...] [options]

Options:
  -s, --serial <serial>     Target device serial
  -p, --package <pkg>       Package name to inspect (required)
  -c, --class <fqcn>        Probe one fully-qualified class name; repeatable
  -o, --outdir <dir>        Output directory (default: ./cnfe_triage_<ts>_<pkg>)
  -h, --help                Show help

What it captures:
  - pm path / pm art dump anchors
  - explicit oatdump header from the live odex/oat artifact
  - oatdump class probes for each requested class
  - pulled live odex + vdex binaries
  - strict vdexdump_min.py JSON for the pulled vdex
  - zero-run / full-zero-page scan for the pulled binaries
  - xxd head + around the largest zero run in each pulled binary

Assumption:
  - This workflow assumes the APK itself is already known-good.
    It uses the APK path only as the ART/oatdump locator; it does not
    re-prove APK integrity.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial)
      SERIAL="${2:-}"
      shift 2
      ;;
    -p|--package)
      PKG="${2:-}"
      shift 2
      ;;
    -c|--class)
      PROBE_CLASSES+=("${2:-}")
      shift 2
      ;;
    -o|--outdir)
      OUTDIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$PKG" ]] || die "--package is required"

TS="$(date +%Y%m%d_%H%M%S)"
SAFE_PKG="${PKG//[^a-zA-Z0-9._-]/_}"
OUTDIR="${OUTDIR:-./cnfe_triage_${TS}_${SAFE_PKG}}"
mkdir -p "$OUTDIR"

export SERIAL
source "$SCRIPT_DIR/adb_helpers.sh"

RUN_DEVICE_OATDUMP="$SCRIPT_DIR/run_device_oatdump.sh"
VDEXDUMP_MIN="$SCRIPT_DIR/vdexdump_min.py"

[[ -x "$RUN_DEVICE_OATDUMP" ]] || die "missing executable: $RUN_DEVICE_OATDUMP"
[[ -f "$VDEXDUMP_MIN" ]] || die "missing parser: $VDEXDUMP_MIN"

if ! adb_host shell 'su -c id' >/dev/null 2>&1; then
  die "root is required from adb shell for odex/vdex triage"
fi

APK_PATH="$(
  adb_sh pm path "$PKG" \
    | tr -d '\r' \
    | sed -n 's/^package://p' \
    | head -n1
)"
[[ -n "$APK_PATH" ]] || die "failed to resolve APK path for package: $PKG"

adb_sh pm art dump "$PKG" | tr -d '\r' > "$OUTDIR/pm_art_dump.txt"

OAT_PATH="$(
  sed -n 's/^.*\[location is \(.*\)\]$/\1/p' "$OUTDIR/pm_art_dump.txt" \
    | head -n1
)"
[[ -n "$OAT_PATH" ]] || die "failed to resolve oat location from pm art dump"

case "$OAT_PATH" in
  *.dex) VDEX_PATH="${OAT_PATH%.dex}.vdex" ;;
  *.odex) VDEX_PATH="${OAT_PATH%.odex}.vdex" ;;
  *.oat) VDEX_PATH="${OAT_PATH%.oat}.vdex" ;;
  *) die "unsupported oat location suffix for VDEX derivation: $OAT_PATH" ;;
esac

{
  echo "serial=${SERIAL:-<default>}"
  echo "package=$PKG"
  echo "apk_path=$APK_PATH"
  echo "oat_path=$OAT_PATH"
  echo "vdex_path=$VDEX_PATH"
} > "$OUTDIR/meta.txt"

oatdump_args=()
if [[ -n "$SERIAL" ]]; then
  oatdump_args+=(--serial "$SERIAL")
fi

set +e
bash "$RUN_DEVICE_OATDUMP" \
  "${oatdump_args[@]}" \
  --oat-file "$OAT_PATH" \
  --apk-path "$APK_PATH" \
  --mode header \
  --out "$OUTDIR/oat.header.txt" \
  > /dev/null 2> "$OUTDIR/oat.header.stderr.txt"
rc=$?
set -e
echo "$rc" > "$OUTDIR/oat.header.rc"

for class_name in "${PROBE_CLASSES[@]}"; do
  safe_class="${class_name//[^a-zA-Z0-9._-]/_}"
  out_txt="$OUTDIR/probe.${safe_class}.txt"
  err_txt="$OUTDIR/probe.${safe_class}.stderr.txt"
  rc_txt="$OUTDIR/probe.${safe_class}.rc"
  set +e
  bash "$RUN_DEVICE_OATDUMP" \
    "${oatdump_args[@]}" \
    --oat-file "$OAT_PATH" \
    --apk-path "$APK_PATH" \
    --mode list-classes \
    --class-filter "$class_name" \
    --require-match \
    --out "$out_txt" \
    > /dev/null 2> "$err_txt"
  rc=$?
  set -e
  echo "$rc" > "$rc_txt"
done

adb_exec_out_su "cat '$OAT_PATH'" > "$OUTDIR/live.odex"
adb_exec_out_su "cat '$VDEX_PATH'" > "$OUTDIR/live.vdex"

set +e
python3 "$VDEXDUMP_MIN" --json --strict "$OUTDIR/live.vdex" > "$OUTDIR/live.vdex.json" 2> "$OUTDIR/live.vdex.stderr.txt"
rc=$?
set -e
echo "$rc" > "$OUTDIR/live.vdex.rc"

python3 - "$OUTDIR" <<'PY'
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
page = 4096
summary = {}

for name in ("live.odex", "live.vdex"):
    path = outdir / name
    data = path.read_bytes()
    max_run = 0
    max_start = 0
    cur = 0
    cur_start = 0
    zero_pages = []
    for idx in range(0, len(data), page):
        chunk = data[idx:idx + page]
        if len(chunk) == page and all(b == 0 for b in chunk):
            zero_pages.append(idx)
    for idx, b in enumerate(data):
        if b == 0:
            if cur == 0:
                cur_start = idx
            cur += 1
            if cur > max_run:
                max_run = cur
                max_start = cur_start
        else:
            cur = 0
    summary[name] = {
        "size": len(data),
        "max_zero_run": max_run,
        "max_zero_run_start": max_start,
        "max_zero_run_page_aligned": (max_start % page == 0),
        "full_zero_page_count": len(zero_pages),
        "full_zero_pages_first10": zero_pages[:10],
    }

(outdir / "zero_scan.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

with (outdir / "zero_scan.txt").open("w", encoding="utf-8") as handle:
    for name, info in summary.items():
        handle.write(
            f"{name} size={info['size']} "
            f"max_zero_run={info['max_zero_run']} "
            f"start={info['max_zero_run_start']} "
            f"page_aligned={info['max_zero_run_page_aligned']} "
            f"full_zero_page_count={info['full_zero_page_count']} "
            f"first_zero_pages={info['full_zero_pages_first10']}\n"
        )
PY

xxd -g 1 -l 128 "$OUTDIR/live.odex" > "$OUTDIR/live.odex.head.hex.txt"
xxd -g 1 -l 128 "$OUTDIR/live.vdex" > "$OUTDIR/live.vdex.head.hex.txt"

python3 - "$OUTDIR" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
summary = json.loads((outdir / "zero_scan.json").read_text(encoding="utf-8"))

for name in ("live.odex", "live.vdex"):
    info = summary[name]
    start = max(0, info["max_zero_run_start"] - 64)
    out_path = outdir / f"{name}.max_zero_run.hex.txt"
    with out_path.open("w", encoding="utf-8") as handle:
        subprocess.run(
            ["xxd", "-g", "1", "-s", str(start), "-l", "256", str(outdir / name)],
            check=True,
            stdout=handle,
        )
PY

{
  echo "package=$PKG"
  echo "apk_path=$APK_PATH"
  echo "oat_path=$OAT_PATH"
  echo "vdex_path=$VDEX_PATH"
  echo "oat_header_rc=$(cat "$OUTDIR/oat.header.rc")"
  echo "vdex_strict_rc=$(cat "$OUTDIR/live.vdex.rc")"
  echo
  cat "$OUTDIR/zero_scan.txt"
  if [[ ${#PROBE_CLASSES[@]} -gt 0 ]]; then
    echo
    echo "class_probe_rcs:"
    for class_name in "${PROBE_CLASSES[@]}"; do
      safe_class="${class_name//[^a-zA-Z0-9._-]/_}"
      echo "  $class_name rc=$(cat "$OUTDIR/probe.${safe_class}.rc")"
    done
  fi
} > "$OUTDIR/summary.txt"

echo "Done: $OUTDIR"
