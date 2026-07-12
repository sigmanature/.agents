#!/usr/bin/env bash
set -u -o pipefail

A_BASE=${A_BASE:-/media/nzzhao/bdb8bfc4-b802-4600-ad17-922826aef12d/android17-ab/aosp-lib-ab-baseline-20260706-210427}
ANDROID_ROOT=${ANDROID_ROOT:-/home/nzzhao/learn_os/android17}
PRODUCT_OUT=${PRODUCT_OUT:-$ANDROID_ROOT/out/target/product/vsoc_x86_64}
RUN_DIR=${RUN_DIR:-/home/nzzhao/cf_runs/userdebug_test}
OUT_DIR=${OUT_DIR:-$ANDROID_ROOT/.worklog/cvd-ab-preflight-$(date +%Y%m%d-%H%M%S)}
A_IMAGE_DIR=${A_IMAGE_DIR:-}
B_IMAGE_DIR=${B_IMAGE_DIR:-}
COMMON_IMAGE_DIR=${COMMON_IMAGE_DIR:-}
B_STABLE_DIR=${B_STABLE_DIR:-}
PRODUCT_IMG=${PRODUCT_IMG:-$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_product_image/android_common/product.img}
SYSTEM_EXT_IMG=${SYSTEM_EXT_IMG:-$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_system_ext_image/android_common/system_ext.img}
ODM_IMG=${ODM_IMG:-$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_odm_image/android_common/odm.img}
VENDOR_IMG=${VENDOR_IMG:-$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_vendor_image/android_common/vendor.img}
ODM_DLKM_IMG=${ODM_DLKM_IMG:-$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_odm_dlkm_image/android_common/odm_dlkm.img}
VENDOR_BOOT_IMAGE=${VENDOR_BOOT_IMAGE:-$RUN_DIR/vendor_boot.img}

failures=0
warns=0

note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }
warn() { printf 'WARN: %s\n' "$*" >&2; warns=$((warns + 1)); }
need_file() { [ -f "$1" ] || fail "missing file: $1"; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

check_system_erofs_layout() {
  local label=$1
  local image=$2
  local dump_erofs=$3

  [ -f "$image" ] || return 0

  "$dump_erofs" --path=/system/bin/init "$image" >"$OUT_DIR/${label}.system-bin-init.txt" 2>&1
  local init_rc=$?
  "$dump_erofs" --path=/system/apex/com.android.runtime.apex "$image" >"$OUT_DIR/${label}.runtime-apex.txt" 2>&1
  local runtime_rc=$?
  "$dump_erofs" --path=/system/system/bin/init "$image" >"$OUT_DIR/${label}.nested-system-bin-init.txt" 2>&1
  local nested_rc=$?

  if [ "$init_rc" -ne 0 ] || ! rg -q 'Access: 0755|regular file' "$OUT_DIR/${label}.system-bin-init.txt"; then
    fail "$label system.img does not expose executable /system/bin/init; see $OUT_DIR/${label}.system-bin-init.txt"
  fi
  if [ "$runtime_rc" -ne 0 ] || ! rg -q 'regular file' "$OUT_DIR/${label}.runtime-apex.txt"; then
    fail "$label system.img does not expose /system/apex/com.android.runtime.apex; see $OUT_DIR/${label}.runtime-apex.txt"
  fi
  if [ "$nested_rc" -eq 0 ] && rg -q 'regular file|directory' "$OUT_DIR/${label}.nested-system-bin-init.txt"; then
    fail "$label system.img appears packaged one directory too high: /system/system/bin/init exists"
  fi
}

extract_hashtree_descriptor() {
  local image=$1
  local partition=$2
  local output=$3
  local avbtool=$4

  "$avbtool" info_image --image "$image" | awk -v partition="$partition" '
    /Hashtree descriptor:/ {
      if (in_block && found) {
        printf "%s", block
      }
      in_block = 1
      found = 0
      block = ""
      next
    }
    in_block && /^[[:space:]]*(Prop:|Hash descriptor:|Kernel cmdline descriptor:|Chain Partition descriptor:)/ {
      if (found) {
        printf "%s", block
      }
      in_block = 0
      found = 0
      block = ""
      next
    }
    in_block {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      if (line == "Partition Name:        " partition) {
        found = 1
      }
      if (line ~ /^(Image Size|Tree Offset|Tree Size|Data Block Size|Hash Block Size|FEC num roots|FEC offset|FEC size|Hash Algorithm|Partition Name|Salt|Root Digest|Flags):/) {
        block = block line "\n"
      }
    }
    END {
      if (in_block && found) {
        printf "%s", block
      }
    }
  ' >"$output"
}

check_vbmeta_system_descriptors() {
  local label=$1
  local vbmeta=$2
  local system_image=$3
  local avbtool=$4
  local image_desc vbmeta_desc partition image

  need_file "$PRODUCT_IMG"
  need_file "$system_image"
  need_file "$SYSTEM_EXT_IMG"
  need_file "$vbmeta"

  for entry in \
    "product:$PRODUCT_IMG" \
    "system:$system_image" \
    "system_ext:$SYSTEM_EXT_IMG"; do
    partition=${entry%%:*}
    image=${entry#*:}
    image_desc="$OUT_DIR/${label}.${partition}.image-hashtree.txt"
    vbmeta_desc="$OUT_DIR/${label}.${partition}.vbmeta-hashtree.txt"
    extract_hashtree_descriptor "$image" "$partition" "$image_desc" "$avbtool"
    extract_hashtree_descriptor "$vbmeta" "$partition" "$vbmeta_desc" "$avbtool"
    if [ ! -s "$image_desc" ]; then
      fail "$label $partition image has no AVB hashtree descriptor; see $image_desc"
      continue
    fi
    if [ ! -s "$vbmeta_desc" ]; then
      fail "$label vbmeta_system lacks $partition hashtree descriptor; see $vbmeta_desc"
      continue
    fi
    if ! cmp -s "$image_desc" "$vbmeta_desc"; then
      diff -u "$image_desc" "$vbmeta_desc" >"$OUT_DIR/${label}.${partition}.hashtree.diff" || true
      fail "$label vbmeta_system $partition descriptor does not match image footer; see $OUT_DIR/${label}.${partition}.hashtree.diff"
    fi
  done
}

check_vbmeta_descriptors() {
  local label=$1
  local vbmeta=$2
  local avbtool=$3
  local image_desc vbmeta_desc partition image

  need_file "$ODM_IMG"
  need_file "$VENDOR_IMG"
  need_file "$ODM_DLKM_IMG"
  need_file "$vbmeta"

  for entry in \
    "odm:$ODM_IMG" \
    "vendor:$VENDOR_IMG" \
    "odm_dlkm:$ODM_DLKM_IMG"; do
    partition=${entry%%:*}
    image=${entry#*:}
    image_desc="$OUT_DIR/${label}.${partition}.image-hashtree.txt"
    vbmeta_desc="$OUT_DIR/${label}.${partition}.vbmeta-hashtree.txt"
    extract_hashtree_descriptor "$image" "$partition" "$image_desc" "$avbtool"
    extract_hashtree_descriptor "$vbmeta" "$partition" "$vbmeta_desc" "$avbtool"
    if [ ! -s "$image_desc" ]; then
      fail "$label $partition image has no AVB hashtree descriptor; see $image_desc"
      continue
    fi
    if [ ! -s "$vbmeta_desc" ]; then
      fail "$label vbmeta lacks $partition hashtree descriptor; see $vbmeta_desc"
      continue
    fi
    if ! cmp -s "$image_desc" "$vbmeta_desc"; then
      diff -u "$image_desc" "$vbmeta_desc" >"$OUT_DIR/${label}.${partition}.hashtree.diff" || true
      fail "$label vbmeta $partition descriptor does not match image footer; see $OUT_DIR/${label}.${partition}.hashtree.diff"
    fi
  done
}

check_vbmeta_is_signed() {
  local label=$1
  local image=$2
  local avbtool=$3
  local info="$OUT_DIR/${label}.vbmeta-info.txt"

  need_file "$image"
  "$avbtool" info_image --image "$image" >"$info" 2>&1 || {
    fail "$label is not a readable vbmeta image; see $info"
    return
  }
  if rg -q '^Algorithm:[[:space:]]+NONE$' "$info"; then
    fail "$label uses Algorithm NONE but is chained from top-level vbmeta; see $info"
  fi
}

mkdir -p "$OUT_DIR"

need_cmd rg
need_cmd sha256sum
need_file "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool"
need_file "$A_BASE/SHA256SUMS"
need_file "$A_BASE/com.android.runtime.apex.baseline"
need_file "$A_BASE/com.android.art.apex.baseline"
need_file "$A_BASE/libc.so.baseline"
need_file "$A_BASE/libart.so.baseline"
if [ -n "$B_STABLE_DIR" ]; then
  need_file "$B_STABLE_DIR/apex/com.android.runtime.apex.B"
  need_file "$B_STABLE_DIR/apex/com.android.art.apex.B"
  need_file "$PRODUCT_OUT/vbmeta.img"
  if [ -n "$B_IMAGE_DIR" ]; then
    need_file "$B_IMAGE_DIR/super.img"
    need_file "$B_IMAGE_DIR/vbmeta_system.img"
  else
    need_file "$B_STABLE_DIR/super.img"
    need_file "$B_STABLE_DIR/vbmeta_system.img"
  fi
else
  need_file "$PRODUCT_OUT/system/apex/com.android.runtime.apex"
  need_file "$PRODUCT_OUT/system/apex/com.android.art.apex"
  need_file "$PRODUCT_OUT/super.img"
  need_file "$PRODUCT_OUT/vbmeta.img"
  need_file "$PRODUCT_OUT/vbmeta_system.img"
fi
need_file "$RUN_DIR/boot.img"
need_file "$RUN_DIR/vendor_boot.img"
need_file "$RUN_DIR/super.img"

archive_root=$(dirname "$A_BASE")
if rg --files "$archive_root" | rg '/aosp-lib-ab-B-' >/dev/null; then
  fail "stale B snapshots still exist under $archive_root"
fi

if [ -e "$ANDROID_ROOT/.worklog/aosp-lib-ab-baseline-20260706-210427" ]; then
  :
else
  warn "baseline .worklog symlink is absent: $ANDROID_ROOT/.worklog/aosp-lib-ab-baseline-20260706-210427"
fi

if [ -f "$A_BASE/SHA256SUMS" ]; then
  (cd "$ANDROID_ROOT" && sha256sum -c "$A_BASE/SHA256SUMS") >"$OUT_DIR/A.sha256check.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "baseline SHA256SUMS check failed; see $OUT_DIR/A.sha256check.log"
  fi
fi

if [ "$failures" -eq 0 ]; then
  sha256sum \
    "$A_BASE/com.android.runtime.apex.baseline" \
    "$A_BASE/com.android.art.apex.baseline" \
    "$A_BASE/libc.so.baseline" \
    "$A_BASE/libart.so.baseline" > "$OUT_DIR/A.inputs.manifest.txt"

  if [ -n "$B_STABLE_DIR" ]; then
    b_super=$B_STABLE_DIR/super.img
    b_vbmeta_system=$B_STABLE_DIR/vbmeta_system.img
    if [ -n "$B_IMAGE_DIR" ]; then
      b_super=$B_IMAGE_DIR/super.img
      b_vbmeta_system=$B_IMAGE_DIR/vbmeta_system.img
    fi
    sha256sum \
      "$B_STABLE_DIR/apex/com.android.runtime.apex.B" \
      "$B_STABLE_DIR/apex/com.android.art.apex.B" \
      "$b_super" \
      "$PRODUCT_OUT/vbmeta.img" \
      "$b_vbmeta_system" > "$OUT_DIR/B.current.manifest.txt"
  else
    sha256sum \
      "$PRODUCT_OUT/system/apex/com.android.runtime.apex" \
      "$PRODUCT_OUT/system/apex/com.android.art.apex" \
      "$PRODUCT_OUT/super.img" \
      "$PRODUCT_OUT/vbmeta.img" \
      "$PRODUCT_OUT/vbmeta_system.img" > "$OUT_DIR/B.current.manifest.txt"
  fi

  sha256sum \
    "$RUN_DIR/boot.img" \
    "$RUN_DIR/vendor_boot.img" \
    "$RUN_DIR/super.img" \
    "$RUN_DIR/vbmeta_system_dlkm.img" \
    "$RUN_DIR/vbmeta_vendor_dlkm.img" > "$OUT_DIR/common.run_dir.manifest.txt" 2>"$OUT_DIR/common.run_dir.manifest.err" || \
      warn "some run-dir DLKM vbmeta files were absent; see $OUT_DIR/common.run_dir.manifest.err"

  a_runtime=$(sha256sum "$A_BASE/com.android.runtime.apex.baseline" | awk '{print $1}')
  a_art=$(sha256sum "$A_BASE/com.android.art.apex.baseline" | awk '{print $1}')
  if [ -n "$B_STABLE_DIR" ]; then
    b_runtime=$(sha256sum "$B_STABLE_DIR/apex/com.android.runtime.apex.B" | awk '{print $1}')
    b_art=$(sha256sum "$B_STABLE_DIR/apex/com.android.art.apex.B" | awk '{print $1}')
  else
    b_runtime=$(sha256sum "$PRODUCT_OUT/system/apex/com.android.runtime.apex" | awk '{print $1}')
    b_art=$(sha256sum "$PRODUCT_OUT/system/apex/com.android.art.apex" | awk '{print $1}')
  fi

  if [ "$a_runtime" = "$b_runtime" ]; then
    fail "A and B runtime APEX hashes are identical; A/B variable missing"
  fi
  if [ "$a_art" = "$b_art" ]; then
    warn "A and B ART APEX hashes are identical; ART may not be part of current B variable"
  fi

  dump_erofs="$ANDROID_ROOT/out/host/linux-x86/bin/dump.erofs"
  if [ -x "$dump_erofs" ]; then
    [ -n "$A_IMAGE_DIR" ] && check_system_erofs_layout A "$A_IMAGE_DIR/system.img" "$dump_erofs"
    [ -n "$B_IMAGE_DIR" ] && check_system_erofs_layout B "$B_IMAGE_DIR/system.img" "$dump_erofs"
  elif [ -n "$A_IMAGE_DIR$B_IMAGE_DIR" ]; then
    warn "dump.erofs is absent; cannot validate final system.img layout"
  fi

  avbtool="$ANDROID_ROOT/out/host/linux-x86/bin/avbtool"
  [ -n "$A_IMAGE_DIR" ] && check_vbmeta_system_descriptors A "$A_IMAGE_DIR/vbmeta_system.img" "$A_IMAGE_DIR/system.img" "$avbtool"
  [ -n "$B_IMAGE_DIR" ] && check_vbmeta_system_descriptors B "$B_IMAGE_DIR/vbmeta_system.img" "$B_IMAGE_DIR/system.img" "$avbtool"
  if [ -n "$COMMON_IMAGE_DIR" ]; then
    need_file "$COMMON_IMAGE_DIR/vendor_boot.img"
    "$avbtool" info_image --image "$COMMON_IMAGE_DIR/vendor_boot.img" >"$OUT_DIR/common.vendor_boot.avb-info.txt" 2>&1 || \
      fail "common vendor_boot.img has no AVB footer; see $OUT_DIR/common.vendor_boot.avb-info.txt"
    check_vbmeta_descriptors common "$COMMON_IMAGE_DIR/vbmeta.img" "$avbtool"
    check_vbmeta_is_signed common-vbmeta "$COMMON_IMAGE_DIR/vbmeta.img" "$avbtool"
    check_vbmeta_is_signed common-vbmeta-system-dlkm "$COMMON_IMAGE_DIR/vbmeta_system_dlkm.img" "$avbtool"
    check_vbmeta_is_signed common-vbmeta-vendor-dlkm "$COMMON_IMAGE_DIR/vbmeta_vendor_dlkm.img" "$avbtool"
  fi
fi

cat > "$OUT_DIR/summary.txt" <<SUMMARY
A_BASE=$A_BASE
PRODUCT_OUT=$PRODUCT_OUT
RUN_DIR=$RUN_DIR
OUT_DIR=$OUT_DIR
B_STABLE_DIR=$B_STABLE_DIR
failures=$failures
warnings=$warns
SUMMARY

if [ "$failures" -eq 0 ]; then
  note "PASS: A/B preflight succeeded; artifacts in $OUT_DIR"
  [ "$warns" -eq 0 ] || note "WARNINGS: $warns"
  exit 0
fi

note "FAIL: A/B preflight found $failures failure(s); artifacts in $OUT_DIR" >&2
exit 1
