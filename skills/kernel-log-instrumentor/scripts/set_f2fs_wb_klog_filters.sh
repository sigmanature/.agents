#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
set_f2fs_wb_klog_filters.sh

Set f2fs sysfs knobs for inode/range filtered writeback KV logs.

These knobs are expected under:
  /sys/fs/f2fs/<s_id>/klog_wb_*

Usage:
  set_f2fs_wb_klog_filters.sh --sid <s_id> --enable 1 --ino <ino> [--idx-lo <n>] [--idx-hi <n>] [--detail 1|2] [--sample <n>]
  set_f2fs_wb_klog_filters.sh --sid <s_id> --enable 0

Options:
  --sid <s>        f2fs instance id (e.g. userdata)
  --enable <0|1>   enable logs
  --detail <0|1|2> 0 errors-only, 1 + enter/exit, 2 + sampled folios
  --sample <n>     per-folio sample rate (0 disables)
  --ino <ino>      inode filter (0 means all)
  --idx-lo <n>     page index low bound (0 disables)
  --idx-hi <n>     page index high bound (0 disables)
  --print-adb      print equivalent 'adb shell su -c ...' instead of executing
EOF
}

SID=""
ENABLE=""
DETAIL=""
SAMPLE=""
INO=""
IDX_LO=""
IDX_HI=""
PRINT_ADB=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sid) SID="${2:-}"; shift 2 ;;
    --enable) ENABLE="${2:-}"; shift 2 ;;
    --detail) DETAIL="${2:-}"; shift 2 ;;
    --sample) SAMPLE="${2:-}"; shift 2 ;;
    --ino) INO="${2:-}"; shift 2 ;;
    --idx-lo) IDX_LO="${2:-}"; shift 2 ;;
    --idx-hi) IDX_HI="${2:-}"; shift 2 ;;
    --print-adb) PRINT_ADB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$SID" || -z "$ENABLE" ]]; then
  echo "ERROR: --sid and --enable are required" >&2
  exit 1
fi

ROOT="/sys/fs/f2fs/$SID"

write_knob() {
  local knob="$1"
  local val="$2"
  if [[ $PRINT_ADB -eq 1 ]]; then
    printf "adb shell su -c %q\n" "echo '$val' > '$ROOT/$knob'"
  else
    echo "$val" > "$ROOT/$knob"
  fi
}

write_knob klog_wb_enable "$ENABLE"

# When disabling, leave other knobs untouched.
if [[ "$ENABLE" == "0" ]]; then
  exit 0
fi

if [[ -n "$DETAIL" ]]; then
  write_knob klog_wb_detail "$DETAIL"
fi
if [[ -n "$SAMPLE" ]]; then
  write_knob klog_wb_sample "$SAMPLE"
fi
if [[ -n "$INO" ]]; then
  write_knob klog_wb_ino "$INO"
fi
if [[ -n "$IDX_LO" ]]; then
  write_knob klog_wb_idx_lo "$IDX_LO"
fi
if [[ -n "$IDX_HI" ]]; then
  write_knob klog_wb_idx_hi "$IDX_HI"
fi

echo "OK"

