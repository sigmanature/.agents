#!/usr/bin/env bash
set -u -o pipefail

ANDROID_ROOT=${ANDROID_ROOT:-/home/nzzhao/learn_os/android17}
IMAGE_WORK_DIR=${IMAGE_WORK_DIR:-}
KERNEL_DIST=${KERNEL_DIST:-/home/nzzhao/learn_os/pixel/out/kernel_x86_64/dist}
TEMPLATE_RUN_DIR=${TEMPLATE_RUN_DIR:-/home/nzzhao/cf_runs/userdebug_test}
RUN_A=${RUN_A:-/home/nzzhao/cf_runs/userdebug_A}
RUN_B=${RUN_B:-/home/nzzhao/cf_runs/userdebug_B}
HOME_A=${HOME_A:-/home/nzzhao/cvd_homes/A}
HOME_B=${HOME_B:-/home/nzzhao/cvd_homes/B}
USERDATA_ROOT=${USERDATA_ROOT:-/media/nzzhao/bdb8bfc4-b802-4600-ad17-922826aef12d/cvd-userdata}
ADB=${ADB:-$ANDROID_ROOT/out/host/linux-x86/bin/adb}
CVD_LAUNCH=${CVD_LAUNCH:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/cvd_launch_with_adb_proxy.sh}
STOP_CVD=${STOP_CVD:-1}
OUT_DIR=${OUT_DIR:-$ANDROID_ROOT/.worklog/cvd-ab-sync/$(date +%Y%m%d-%H%M%S)}
LOCK=${LOCK:-/tmp/cvd-sync-ab-run-dirs-${USER:-user}.lock}

mkdir -p "$OUT_DIR"
LOG=$OUT_DIR/sync.log

log() { printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }
fail() { log "FAIL: $*"; return 1; }
need_file() { [ -f "$1" ] || fail "missing file: $1"; }
realpath_maybe() { readlink -f "$1" 2>/dev/null || true; }

stop_profile() {
  local name=$1 home_dir=$2 run_dir=$3 port=$4 cid=$5 inst=$6
  if [ ! -s "$home_dir/.cuttlefish_config.json" ] && [ ! -d "$home_dir/cuttlefish/instances/cvd-$inst" ]; then
    log "SKIP stop profile=$name uninitialized_home=$home_dir"
    return 0
  fi
  log "STOP profile=$name"
  HOME="$home_dir" RUN_DIR="$run_dir" PORT="$port" CID="$cid" ADB_BIN="$ADB" "$CVD_LAUNCH" stop >>"$LOG" 2>&1 || true
}

run_dir_runtime_cleanup() {
  local dir=$1
  rm -rf \
    "$dir/.cache" \
    "$dir/.cuttlefish_config.json" \
    "$dir/cuttlefish" \
    "$dir/cuttlefish_assembly" \
    "$dir/cuttlefish_runtime" \
    "$dir/cuttlefish_runtime.1"
}

ensure_userdata_image() {
  local profile=$1 run_dir=$2
  local src target_dir target tmp
  src=$(realpath_maybe "$TEMPLATE_RUN_DIR/userdata.img")
  target_dir="$USERDATA_ROOT/userdebug_${profile}"
  target="$target_dir/userdata.img"
  tmp="$target.tmp.$$"
  [ -n "$src" ] && [ -s "$src" ] || fail "missing template userdata: $TEMPLATE_RUN_DIR/userdata.img -> $src" || return 1
  mkdir -p "$target_dir"
  if [ ! -s "$target" ]; then
    log "CREATE userdata profile=$profile src=$src target=$target"
    rm -f "$tmp"
    cp --sparse=always --reflink=auto "$src" "$tmp" || return 1
    mv -f "$tmp" "$target" || return 1
  else
    log "KEEP userdata profile=$profile target=$target"
  fi
  ln -sfn "$target" "$run_dir/userdata.img"
}

link_profile_images() {
  local profile=$1 run_dir=$2 variant_dir=$3
  ln -sfn "$variant_dir/super.img" "$run_dir/super.img"
  ln -sfn "$IMAGE_WORK_DIR/common/vbmeta.img" "$run_dir/vbmeta.img"
  ln -sfn "$variant_dir/vbmeta_system.img" "$run_dir/vbmeta_system.img"
  ln -sfn "$IMAGE_WORK_DIR/common/vbmeta_system_dlkm.img" "$run_dir/vbmeta_system_dlkm.img"
  ln -sfn "$IMAGE_WORK_DIR/common/vbmeta_vendor_dlkm.img" "$run_dir/vbmeta_vendor_dlkm.img"
  if [ -f "$KERNEL_DIST/boot.img" ]; then
    ln -sfn "$KERNEL_DIST/boot.img" "$run_dir/boot.img"
  fi
  if [ -f "$IMAGE_WORK_DIR/common/vendor_boot.img" ]; then
    ln -sfn "$IMAGE_WORK_DIR/common/vendor_boot.img" "$run_dir/vendor_boot.img"
  elif [ -f "$KERNEL_DIST/vendor_boot.img" ]; then
    ln -sfn "$KERNEL_DIST/vendor_boot.img" "$run_dir/vendor_boot.img"
  fi
  log "LINK profile=$profile run_dir=$run_dir"
}

prepared_ok() {
  local a_ud b_ud
  a_ud=$(realpath_maybe "$RUN_A/userdata.img")
  b_ud=$(realpath_maybe "$RUN_B/userdata.img")
  [ -s "$a_ud" ] || return 1
  [ -s "$b_ud" ] || return 1
  [ "$a_ud" != "$b_ud" ] || return 1
  [ "$(realpath_maybe "$RUN_A/super.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/A/super.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_B/super.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/B/super.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_A/vendor_boot.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vendor_boot.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_B/vendor_boot.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vendor_boot.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_A/vbmeta.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vbmeta.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_B/vbmeta.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vbmeta.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_A/vbmeta_system_dlkm.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vbmeta_system_dlkm.img")" ] || return 1
  [ "$(realpath_maybe "$RUN_B/vbmeta_system_dlkm.img")" = "$(realpath_maybe "$IMAGE_WORK_DIR/common/vbmeta_system_dlkm.img")" ] || return 1
}

write_manifest() {
  {
    echo "IMAGE_WORK_DIR=$IMAGE_WORK_DIR"
    echo "KERNEL_DIST=$KERNEL_DIST"
    for f in \
      "$RUN_A"/boot.img "$RUN_A"/vendor_boot.img "$RUN_A"/super.img "$RUN_A"/vbmeta.img "$RUN_A"/vbmeta_system.img "$RUN_A"/vbmeta_system_dlkm.img "$RUN_A"/vbmeta_vendor_dlkm.img "$RUN_A"/userdata.img \
      "$RUN_B"/boot.img "$RUN_B"/vendor_boot.img "$RUN_B"/super.img "$RUN_B"/vbmeta.img "$RUN_B"/vbmeta_system.img "$RUN_B"/vbmeta_system_dlkm.img "$RUN_B"/vbmeta_vendor_dlkm.img "$RUN_B"/userdata.img; do
      printf '%s -> %s\n' "$f" "$(realpath_maybe "$f")"
    done
    sha256sum \
      "$RUN_A"/boot.img "$RUN_A"/vendor_boot.img "$RUN_A"/super.img "$RUN_A"/vbmeta.img "$RUN_A"/vbmeta_system.img "$RUN_A"/vbmeta_system_dlkm.img "$RUN_A"/vbmeta_vendor_dlkm.img \
      "$RUN_B"/boot.img "$RUN_B"/vendor_boot.img "$RUN_B"/super.img "$RUN_B"/vbmeta.img "$RUN_B"/vbmeta_system.img "$RUN_B"/vbmeta_system_dlkm.img "$RUN_B"/vbmeta_vendor_dlkm.img 2>/dev/null || true
  } > "$OUT_DIR/run_dirs_manifest.txt"
}

sync_run_dirs() {
  [ -n "$IMAGE_WORK_DIR" ] || fail "IMAGE_WORK_DIR is required" || return 2
  need_file "$IMAGE_WORK_DIR/A/super.img" || return 2
  need_file "$IMAGE_WORK_DIR/A/vbmeta_system.img" || return 2
  need_file "$IMAGE_WORK_DIR/B/super.img" || return 2
  need_file "$IMAGE_WORK_DIR/B/vbmeta_system.img" || return 2
  need_file "$IMAGE_WORK_DIR/common/vendor_boot.img" || return 2
  need_file "$IMAGE_WORK_DIR/common/vbmeta.img" || return 2
  need_file "$IMAGE_WORK_DIR/common/vbmeta_system_dlkm.img" || return 2
  need_file "$IMAGE_WORK_DIR/common/vbmeta_vendor_dlkm.img" || return 2
  need_file "$KERNEL_DIST/boot.img" || return 2
  need_file "$TEMPLATE_RUN_DIR/vendor_boot.img" || return 2

  if [ "$STOP_CVD" = 1 ]; then
    stop_profile A "$HOME_A" "$RUN_A" 16521 3 1
    stop_profile B "$HOME_B" "$RUN_B" 16522 4 2
  fi

  for d in "$RUN_A" "$RUN_B"; do
    mkdir -p "$d"
    log "RSYNC template -> $d"
    rsync -a --delete "$TEMPLATE_RUN_DIR/" "$d/" >>"$LOG" 2>&1 || return 3
    run_dir_runtime_cleanup "$d"
  done
  ensure_userdata_image A "$RUN_A" || return 4
  ensure_userdata_image B "$RUN_B" || return 4
  link_profile_images A "$RUN_A" "$IMAGE_WORK_DIR/A"
  link_profile_images B "$RUN_B" "$IMAGE_WORK_DIR/B"
  mkdir -p "$HOME_A" "$HOME_B"
  write_manifest
  prepared_ok || fail "prepared_ok failed; see $OUT_DIR/run_dirs_manifest.txt" || return 5
  log "PASS synced A/B run dirs; manifest=$OUT_DIR/run_dirs_manifest.txt"
}

status_cmd() {
  echo "OUT_DIR=$OUT_DIR"
  echo "IMAGE_WORK_DIR=$IMAGE_WORK_DIR"
  echo "RUN_A=$RUN_A -> $(realpath_maybe "$RUN_A/super.img")"
  echo "RUN_B=$RUN_B -> $(realpath_maybe "$RUN_B/super.img")"
  if [ -n "$IMAGE_WORK_DIR" ]; then
    prepared_ok && echo "prepared_ok=1" || echo "prepared_ok=0"
  fi
}

cmd=${1:-sync}
case "$cmd" in
  sync|run)
    exec 9>"$LOCK"
    if ! flock -n 9; then
      echo "FAIL: another sync holds $LOCK" >&2
      exit 1
    fi
    sync_run_dirs
    exit $?
    ;;
  status) status_cmd ;;
  *) echo "usage: $0 {sync|status}" >&2; exit 64 ;;
esac
