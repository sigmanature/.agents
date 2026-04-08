#!/usr/bin/env bash
set -euo pipefail

VMLINUX=""
SYSTEM_MAP=""
SYM=""
OFF=""
WINDOW_BEFORE=0x80
WINDOW_AFTER=0x120

usage() {
  cat <<'EOF'
Usage:
  symoff_lookup.sh --vmlinux <VMLINUX> --sym <FUNC> --off <0xOFF|DEC> [--system-map <System.map>]

Example:
  symoff_lookup.sh \
    --vmlinux ~/learn_os/pixel/out/cache/last_build/common/vmlinux \
    --system-map ~/learn_os/pixel/out/cache/last_build/common/System.map \
    --sym f2fs_evict_inode \
    --off 0x5b0

What it does:
  1) resolve FUNC base address (System.map preferred; nm fallback)
  2) compute ADDR = base + off
  3) print addr2line result (may point to trace/events for inlines)
  4) print a small objdump window with -dl (line annotations)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vmlinux) VMLINUX="${2:-}"; shift 2;;
    --system-map) SYSTEM_MAP="${2:-}"; shift 2;;
    --sym) SYM="${2:-}"; shift 2;;
    --off) OFF="${2:-}"; shift 2;;
    --before) WINDOW_BEFORE="${2:-}"; shift 2;;
    --after) WINDOW_AFTER="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$VMLINUX" || -z "$SYM" || -z "$OFF" ]]; then
  echo "missing required args" >&2
  usage
  exit 2
fi

if [[ ! -f "$VMLINUX" ]]; then
  echo "missing vmlinux: $VMLINUX" >&2
  exit 2
fi

pick_addr2line() {
  if command -v aarch64-linux-gnu-addr2line >/dev/null 2>&1; then
    echo aarch64-linux-gnu-addr2line
  else
    echo addr2line
  fi
}

pick_nm() {
  if command -v aarch64-linux-gnu-nm >/dev/null 2>&1; then
    echo aarch64-linux-gnu-nm
  else
    echo nm
  fi
}

pick_objdump() {
  if command -v aarch64-linux-gnu-objdump >/dev/null 2>&1; then
    echo aarch64-linux-gnu-objdump
  else
    echo objdump
  fi
}

ADDR2LINE="$(pick_addr2line)"
NM="$(pick_nm)"
OBJDUMP="$(pick_objdump)"

resolve_base_from_map() {
  local map="$1"
  local sym="$2"
  # Match "ADDR TYPE SYM" (TYPE is usually T/t)
  awk -v s="$sym" '$3==s {print $1; exit 0}' "$map"
}

resolve_base_from_nm() {
  local vmlinux="$1"
  local sym="$2"
  "$NM" -n "$vmlinux" | awk -v s="$sym" '$3==s {print $1; exit 0}'
}

BASE_HEX=""
if [[ -n "$SYSTEM_MAP" && -f "$SYSTEM_MAP" ]]; then
  BASE_HEX="$(resolve_base_from_map "$SYSTEM_MAP" "$SYM" || true)"
fi
if [[ -z "$BASE_HEX" ]]; then
  BASE_HEX="$(resolve_base_from_nm "$VMLINUX" "$SYM" || true)"
fi
if [[ -z "$BASE_HEX" ]]; then
  echo "failed to resolve symbol base: $SYM" >&2
  echo "hint: pass --system-map <System.map> or ensure nm can see symbols" >&2
  exit 1
fi

# Normalize BASE_HEX (may be "ffffff..." without 0x)
BASE=$((0x$BASE_HEX))

# OFF may be 0x.. or decimal
if [[ "$OFF" == 0x* || "$OFF" == 0X* ]]; then
  OFF_N=$((OFF))
else
  OFF_N=$((10#$OFF))
fi

ADDR=$((BASE + OFF_N))
START=$((ADDR - WINDOW_BEFORE))
STOP=$((ADDR + WINDOW_AFTER))

echo "[*] vmlinux: $VMLINUX"
if [[ -n "$SYSTEM_MAP" ]]; then
  echo "[*] system_map: $SYSTEM_MAP"
fi
echo "[*] sym: $SYM"
printf '[*] base: %#x\n' "$BASE"
printf '[*] off:  %#x\n' "$OFF_N"
printf '[*] addr: %#x\n' "$ADDR"
printf '[*] win:  [%#x, %#x)\n' "$START" "$STOP"

echo
echo "[*] addr2line ($ADDR2LINE):"
"$ADDR2LINE" -e "$VMLINUX" -fip "$(printf '%#x' "$ADDR")" || true

echo
echo "[*] objdump ($OBJDUMP) -dl window:"
"$OBJDUMP" -dl --start-address="$START" --stop-address="$STOP" "$VMLINUX" | sed -n '1,200p' || true

