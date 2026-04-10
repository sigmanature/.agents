#!/usr/bin/env bash
set -euo pipefail

# Host-side orchestrator for:
# 1) (optional) install top100 APKs to missing device
# 2) run 9h THP sampler + memstress on two devices
# 3) plot derived.csv to SVG

APK_DIR=${APK_DIR:-./top100_apks}
PKG_LIST=${PKG_LIST:-./all_packages.txt}
DURATION_S=${DURATION_S:-32400}   # 9h
INTERVAL_S=${INTERVAL_S:-60}
JOBS=${JOBS:-2}

SKILL_DIR=${SKILL_DIR:-/home/nzzhao/.agents/skills/android-thp-fallback-sampler}
RUNNER=${RUNNER:-$SKILL_DIR/scripts/run_memstress_and_collect_logs.py}
INSTALLER=${INSTALLER:-$SKILL_DIR/scripts/apk_batch_install.py}
PLOTTER=${PLOTTER:-$SKILL_DIR/scripts/plot_derived_svg.py}

TS=${TS:-$(date +%Y%m%d_%H%M%S)}
OUT_DIR=${OUT_DIR:-./output/thp_memstress_top100_9h_${TS}}

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 2; }
}

require adb
require python3

adb kill-server >/dev/null 2>&1 || true
adb start-server >/dev/null

# Expect exactly 2 devices online.
mapfile -t SERIALS < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
if [[ ${#SERIALS[@]} -ne 2 ]]; then
  echo "Expected 2 devices in adb 'device' state, got ${#SERIALS[@]}" >&2
  adb devices -l >&2 || true
  exit 3
fi

echo "Devices: ${SERIALS[*]}"

# Check install coverage for the package list.
python3 - <<'PY'
import subprocess
from pathlib import Path
pkgs=[ln.strip() for ln in Path('all_packages.txt').read_text().splitlines() if ln.strip()]
serials=subprocess.check_output(['adb','devices']).decode().splitlines()
serials=[ln.split()[0] for ln in serials[1:] if ln.strip().endswith('device')]
for s in serials:
    cp=subprocess.run(['adb','-s',s,'shell','pm','list','packages'],capture_output=True,text=True)
    installed=set()
    for line in cp.stdout.splitlines():
        line=line.strip()
        if line.startswith('package:'):
            installed.add(line[len('package:'):].strip())
    hit=sum(1 for p in pkgs if p in installed)
    miss=len(pkgs)-hit
    print(f"{s} installed_in_list={hit}/{len(pkgs)} missing={miss}")
PY

# Optional: install APKs to each device (installer supports skip-installed).
# If you only want to install to one device, set SERIAL_INSTALL=... in env.
if [[ -n "${SERIAL_INSTALL:-}" ]]; then
  echo "Installing top100 APKs to ${SERIAL_INSTALL} (skip-installed enabled by default)"
  python3 "$INSTALLER" "$APK_DIR" --serial "$SERIAL_INSTALL" --output-dir "./output/apk_install_top100_${TS}_${SERIAL_INSTALL}" --timeout 600 --retries 2 --retry-sleep 3
else
  echo "Installing top100 APKs to both devices (skip-installed enabled by default)"
  for s in "${SERIALS[@]}"; do
    python3 "$INSTALLER" "$APK_DIR" --serial "$s" --output-dir "./output/apk_install_top100_${TS}_${s}" --timeout 600 --retries 2 --retry-sleep 3 || true
  done
fi

echo "Starting 9h memstress run: out_dir=$OUT_DIR"
mkdir -p "$OUT_DIR"
nohup python3 "$RUNNER" \
  --serial "${SERIALS[0]}" --serial "${SERIALS[1]}" \
  --jobs "$JOBS" \
  --out-dir "$OUT_DIR" \
  --duration-s "$DURATION_S" \
  --interval-s "$INTERVAL_S" \
  --package-file "$PKG_LIST" \
  >"$OUT_DIR/host_stdout.txt" 2>"$OUT_DIR/host_stderr.txt" &

echo $! >"$OUT_DIR/host_pid.txt"
echo "Launched. pid=$(cat "$OUT_DIR/host_pid.txt")"

echo "After run completes, plot:"
echo "  python3 $PLOTTER $OUT_DIR/${SERIALS[0]}/derived.csv $OUT_DIR/${SERIALS[1]}/derived.csv --align absolute --out-dir $OUT_DIR/plot"

