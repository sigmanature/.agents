#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=adb_helpers.sh
source "$SCRIPT_DIR/adb_helpers.sh"

PKG=""
SERIAL="${SERIAL:-}"
OUTDIR=""
TOOL_PM_ART_DUMP_SUMMARY="$SCRIPT_DIR/pm_art_dump_summary.py"

usage() {
  cat <<'EOF'
adb_oat_invariant_freeze.sh: capture package-level invariant inputs for oat/vdex experiments

Usage:
  adb_oat_invariant_freeze.sh --package <pkg.name> [options]

Options:
  -s, --serial <serial>   Target device serial
  -p, --package <pkg>     Package name (required)
  -o, --outdir <dir>      Output directory (default: ./oat_invariants_<ts>_<pkg>)
  -h, --help              Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    -p|--package) PKG="${2:-}"; shift 2;;
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

TS="$(date +%Y%m%d_%H%M%S)"
SAFE_PKG="${PKG//[^a-zA-Z0-9._-]/_}"
OUTDIR="${OUTDIR:-./oat_invariants_${TS}_${SAFE_PKG}}"
mkdir -p "$OUTDIR"

{
  echo "serial: ${SERIAL:-<default>}"
  echo "package: $PKG"
  echo "host_wall: $(date +%Y-%m-%dT%H:%M:%S)"
} > "$OUTDIR/meta.txt"

adb_sh date >> "$OUTDIR/meta.txt" || true
adb_sh pm path "$PKG" > "$OUTDIR/pm_path.txt"
adb_sh pm art dump "$PKG" > "$OUTDIR/art_dump.txt" || true
python3 "$TOOL_PM_ART_DUMP_SUMMARY" "$OUTDIR/art_dump.txt" > "$OUTDIR/art_dump_summary.json" 2>/dev/null || true
adb_sh cmd package resolve-activity --brief "$PKG" > "$OUTDIR/resolve_activity.txt" || true

{
  echo "ro.build.fingerprint=$(adb_sh getprop ro.build.fingerprint | tr -d '\r')"
  echo "ro.bootimage.build.fingerprint=$(adb_sh getprop ro.bootimage.build.fingerprint | tr -d '\r')"
  echo "ro.product.cpu.abi=$(adb_sh getprop ro.product.cpu.abi | tr -d '\r')"
  echo "ro.dalvik.vm.native.bridge=$(adb_sh getprop ro.dalvik.vm.native.bridge | tr -d '\r')"
  echo "dalvik.vm.isa.arm64.variant=$(adb_sh getprop dalvik.vm.isa.arm64.variant | tr -d '\r')"
  echo "dalvik.vm.isa.arm64.features=$(adb_sh getprop dalvik.vm.isa.arm64.features | tr -d '\r')"
} > "$OUTDIR/selected_props.txt"

sed -n 's/^package://p' "$OUTDIR/pm_path.txt" | tr -d '\r' > "$OUTDIR/apk_files.txt"
: > "$OUTDIR/apk_meta.txt"
while IFS= read -r apk_path; do
  [[ -z "$apk_path" ]] && continue
  {
    echo "artifact: $apk_path"
    adb_su_sh "ls -li '$apk_path' 2>/dev/null || true"
    adb_su_sh "ls -ln '$apk_path' 2>/dev/null || true"
    adb_su_sh "command -v sha256sum >/dev/null 2>&1 && sha256sum '$apk_path' 2>/dev/null || true"
    echo
  } >> "$OUTDIR/apk_meta.txt"
done < "$OUTDIR/apk_files.txt"

adb_su_sh "find /data/misc/profiles -type f 2>/dev/null | grep '/$PKG/' || true" | tr -d '\r' > "$OUTDIR/profile_files.txt"
: > "$OUTDIR/profile_meta.txt"
while IFS= read -r profile_path; do
  [[ -z "$profile_path" ]] && continue
  {
    echo "artifact: $profile_path"
    adb_su_sh "ls -li '$profile_path' 2>/dev/null || true"
    adb_su_sh "ls -ln '$profile_path' 2>/dev/null || true"
    adb_su_sh "command -v sha256sum >/dev/null 2>&1 && sha256sum '$profile_path' 2>/dev/null || true"
    echo
  } >> "$OUTDIR/profile_meta.txt"
done < "$OUTDIR/profile_files.txt"

echo "Done: $OUTDIR"
