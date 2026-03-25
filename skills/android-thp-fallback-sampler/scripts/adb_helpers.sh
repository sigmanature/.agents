#!/usr/bin/env bash
# Helper wrappers for consistent adb usage in other scripts.
#
# Usage:
#   source ./scripts/adb_helpers.sh
#   SERIAL=ABC123   # optional
#   adb_host logcat -v threadtime
#   adb_sh pm list packages -3
#   adb_sh_sh 'getprop | sort'
#   adb_su 'id'
#   adb_su_sh 'dmesg | tail -n 50'
#   adb_exec_out_su 'cat /data/tombstones/tombstone_00' > tombstone_00

set -euo pipefail

# If set, used as: adb -s "$SERIAL" ...
SERIAL="${SERIAL:-}"

adb_host() {
  if [[ -n "$SERIAL" ]]; then
    adb -s "$SERIAL" "$@"
  else
    adb "$@"
  fi
}

adb_sh() {
  # For simple commands without pipes/redirection/globs.
  adb_host shell "$@"
}

adb_sh_sh() {
  # For commands that need a device shell to interpret metacharacters.
  # Example: adb_sh_sh 'logcat -d | tail -n 50 > /sdcard/tail.txt'
  local cmd="$1"
  adb_host shell sh -c "$cmd"
}

adb_su() {
  # Root for simple commands.
  local cmd="$1"
  adb_host shell su -c "$cmd"
}

_sh_single_quote() {
  # POSIX-sh-safe single-quote wrapper.
  local s="$1"
  s=${s//"'"/"'\\''"}
  printf "'%s'" "$s"
}

adb_su_sh() {
  # Root for commands that need metacharacters interpreted on-device.
  # Example: adb_su_sh 'dmesg | tail -n 50 > /data/local/tmp/dmesg_tail.txt'
  local cmd="$1"
  local q
  q=$(_sh_single_quote "$cmd")
  adb_host shell su -c "sh -c $q"
}

adb_exec_out_su() {
  # Stream stdout from a root command to host (good for files).
  local cmd="$1"
  adb_host exec-out su -c "$cmd"
}
