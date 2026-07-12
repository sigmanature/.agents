#!/usr/bin/env bash
set -u -o pipefail

ANDROID_ROOT=${ANDROID_ROOT:-/home/nzzhao/learn_os/android17}
LEGACY_A_BASE=${LEGACY_A_BASE:-/media/nzzhao/bdb8bfc4-b802-4600-ad17-922826aef12d/android17-ab/aosp-lib-ab-baseline-20260706-210427}
PRISTINE_A_BASE=${PRISTINE_A_BASE:-$ANDROID_ROOT/.worklog/pristine-A-base-20260709-162929}
if [ -z "${A_BASE:-}" ]; then
  if [ -f "$PRISTINE_A_BASE/com.android.runtime.apex.baseline" ] && [ -f "$PRISTINE_A_BASE/com.android.art.apex.baseline" ]; then
    A_BASE=$PRISTINE_A_BASE
  else
    A_BASE=$LEGACY_A_BASE
  fi
fi
PRODUCT_OUT=${PRODUCT_OUT:-$ANDROID_ROOT/out/target/product/vsoc_x86_64}
KERNEL_DIST=${KERNEL_DIST:-/home/nzzhao/learn_os/pixel/out/kernel_x86_64/dist}
VENDOR_DLKM_IMAGE=${VENDOR_DLKM_IMAGE:-$ANDROID_ROOT/.worklog/cvd-custom-vendor-dlkm-fixed-20260708-171533/vendor_dlkm.empty-load.erofs.img}
TEMPLATE_RUN_DIR=${TEMPLATE_RUN_DIR:-/home/nzzhao/cf_runs/userdebug_test}
VENDOR_BOOT_IMAGE=${VENDOR_BOOT_IMAGE:-$TEMPLATE_RUN_DIR/vendor_boot.img}
OUT_ROOT=${OUT_ROOT:-$ANDROID_ROOT/.worklog/cvd-ab-images}
WORK_DIR=${WORK_DIR:-$OUT_ROOT/$(date +%Y%m%d-%H%M%S)-ab-composed}
B_STABLE_DIR=${B_STABLE_DIR:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

LOCK=${LOCK:-/tmp/cvd-compose-ab-images-${USER:-user}.lock}
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "FAIL: another compose run holds $LOCK" >&2
  exit 1
fi

failures=0
note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }
need_file() { [ -f "$1" ] || fail "missing file: $1"; }
run_step() {
  local label=$1
  local logfile=$2
  shift 2
  note "== $label =="
  "$@" >"$logfile" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$label failed rc=$rc; see $logfile"
    tail -80 "$logfile" >&2 || true
  fi
  return "$rc"
}

SHARED=$ANDROID_ROOT/out/soong/.intermediates/build/make/target/product/generic/aosp_shared_system_image/android_common
SYSTEM_PROP=$SHARED/prop
PRODUCT_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_product_image/android_common/product.img
SYSTEM_EXT_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_system_ext_image/android_common/system_ext.img
ODM_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_odm_image/android_common/odm.img
VENDOR_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_vendor_image/android_common/vendor.img
ODM_DLKM_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_odm_dlkm_image/android_common/odm_dlkm.img
SYSTEM_OTHER_IMG=$ANDROID_ROOT/out/soong/.intermediates/build/soong/fsgen/aosp_cf_x86_64_phone_generated_system_other_image/android_common/system_other.img

need_file "$A_BASE/com.android.runtime.apex.baseline"
need_file "$A_BASE/com.android.art.apex.baseline"
need_file "$SHARED/system/system/bin/init"
need_file "$SYSTEM_PROP"
need_file "$PRODUCT_IMG"
need_file "$SYSTEM_EXT_IMG"
need_file "$KERNEL_DIST/system_dlkm.erofs.img"
need_file "$VENDOR_DLKM_IMAGE"
need_file "$VENDOR_BOOT_IMAGE"
need_file "$ODM_IMG"
need_file "$VENDOR_IMG"
need_file "$ODM_DLKM_IMG"
need_file "$SYSTEM_OTHER_IMG"
need_file "$PRODUCT_OUT/system.img"
need_file "$PRODUCT_OUT/super.img"
need_file "$PRODUCT_OUT/vbmeta_system.img"
if [ -n "$B_STABLE_DIR" ]; then
  need_file "$B_STABLE_DIR/apex/com.android.runtime.apex.B"
  need_file "$B_STABLE_DIR/apex/com.android.art.apex.B"
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi

TMP=$WORK_DIR/tmp
COMMON=$WORK_DIR/common
A=$WORK_DIR/A
B=$WORK_DIR/B
mkdir -p "$TMP" "$COMMON" "$A" "$B"

write_super_misc() {
  local outfile=$1
  local system_image=$2
  cat >"$outfile" <<MISC
use_dynamic_partitions=true
lpmake=lpmake
build_super_partition=true
build_super_empty_partition=true
super_metadata_device=super
super_block_devices=super
super_super_device_size=8589934592
dynamic_partition_list=odm odm_dlkm product system system_dlkm system_ext vendor vendor_dlkm
super_partition_groups=google_system_dynamic_partitions google_vendor_dynamic_partitions
super_google_system_dynamic_partitions_group_size=6845104128
super_google_system_dynamic_partitions_partition_list=product system system_ext system_dlkm
super_google_vendor_dynamic_partitions_group_size=1472200704
super_google_vendor_dynamic_partitions_partition_list=odm vendor vendor_dlkm odm_dlkm
super_image_in_update_package=true
super_partition_size=8589934592
virtual_ab=true
virtual_ab_compression=true
virtual_ab_compression_method=lz4
virtual_ab_cow_version=3
virtual_ab_compression_factor=65536
ab_update=true
product_image=$PRODUCT_IMG
system_image=$system_image
system_ext_image=$SYSTEM_EXT_IMG
system_dlkm_image=$KERNEL_DIST/system_dlkm.erofs.img
odm_image=$ODM_IMG
vendor_image=$VENDOR_IMG
vendor_dlkm_image=$VENDOR_DLKM_IMAGE
odm_dlkm_image=$ODM_DLKM_IMG
system_other_image=$SYSTEM_OTHER_IMG
MISC
}

build_super_variant() {
  local label=$1
  local dir=$2
  local system_image=$3

  write_super_misc "$dir/misc_info.super.txt" "$system_image"
  run_step "build $label sparse super.img" "$dir/build-super.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/build_super_image" "$dir/misc_info.super.txt" "$dir/super.img.sparse"
  [ "$failures" -eq 0 ] || return 1
  run_step "convert $label raw super.img" "$dir/simg2img.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/simg2img" "$dir/super.img.sparse" "$dir/super.img"
}

cat >"$WORK_DIR/summary.txt" <<SUMMARY
WORK_DIR=$WORK_DIR
ANDROID_ROOT=$ANDROID_ROOT
A_BASE=$A_BASE
PRODUCT_OUT=$PRODUCT_OUT
KERNEL_DIST=$KERNEL_DIST
VENDOR_DLKM_IMAGE=$VENDOR_DLKM_IMAGE
B_STABLE_DIR=$B_STABLE_DIR
SUMMARY

note "Composing A/B images under $WORK_DIR"
run_step "build common vbmeta_system_dlkm.img" "$COMMON/build-vbmeta-system-dlkm.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
  --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
  --algorithm SHA256_RSA4096 \
  --padding_size 4096 \
  --rollback_index 1780617600 \
  --include_descriptors_from_image "$KERNEL_DIST/system_dlkm.erofs.img" \
  --output "$COMMON/vbmeta_system_dlkm.img"
[ "$failures" -eq 0 ] || exit 1
truncate -s 65536 "$COMMON/vbmeta_system_dlkm.img"

run_step "build common vbmeta_vendor_dlkm.img" "$COMMON/build-vbmeta-vendor-dlkm.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
  --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
  --algorithm SHA256_RSA4096 \
  --padding_size 4096 \
  --rollback_index 1780617600 \
  --include_descriptors_from_image "$VENDOR_DLKM_IMAGE" \
  --output "$COMMON/vbmeta_vendor_dlkm.img"
[ "$failures" -eq 0 ] || exit 1
truncate -s 65536 "$COMMON/vbmeta_vendor_dlkm.img"

run_step "extract common AVB pubkey" "$COMMON/extract-avb-pubkey.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" extract_public_key \
  --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
  --output "$COMMON/testkey.avbpubkey"
[ "$failures" -eq 0 ] || exit 1

VENDOR_BOOT_DESCRIPTOR_ARGS=()
if "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" info_image --image "$VENDOR_BOOT_IMAGE" >"$COMMON/vendor-boot-avb-info.log" 2>&1; then
  ln -sfn "$VENDOR_BOOT_IMAGE" "$COMMON/vendor_boot.img"
  VENDOR_BOOT_DESCRIPTOR_ARGS=(--include_descriptors_from_image "$COMMON/vendor_boot.img")
else
  run_step "compact vendor_boot before AVB footer" "$COMMON/compact-vendor-boot.log" \
    python3 - "$VENDOR_BOOT_IMAGE" "$COMMON/vendor_boot.img" <<'PY'
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
data = src.read_bytes()
last = len(data) - 1
while last >= 0 and data[last] == 0:
    last -= 1
if last < 0:
    raise SystemExit("vendor_boot image is all zeros")
page_size = 2048
size = ((last + 1 + page_size - 1) // page_size) * page_size
dst.write_bytes(data[:size])
print(f"compacted {src} from {len(data)} to {size} bytes")
PY
  [ "$failures" -eq 0 ] || exit 1
  run_step "add AVB footer to vendor_boot" "$COMMON/add-vendor-boot-footer.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" add_hash_footer \
    --image "$COMMON/vendor_boot.img" \
    --partition_name vendor_boot \
    --partition_size 67108864 \
    --salt 4578c4d77a9db454ee82a2dc1e349f100f3c02dfb6b84f6ceee76f98c865b161d1bf8bfb625b94b1d9e4138308fa87f41c3968e312d306195ac4f9d0fa9f9ca9
  [ "$failures" -eq 0 ] || exit 1
  VENDOR_BOOT_DESCRIPTOR_ARGS=(--include_descriptors_from_image "$COMMON/vendor_boot.img")
  {
    echo "WARN: vendor_boot has no readable AVB footer; skipping descriptor include"
    cat "$COMMON/vendor-boot-avb-info.log"
    echo "Generated AVB-footered vendor_boot at $COMMON/vendor_boot.img"
  } >"$COMMON/vendor-boot-avb-generated.log"
fi

run_step "build common vbmeta.img" "$COMMON/build-vbmeta.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
  --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
  --algorithm SHA256_RSA4096 \
  --padding_size 4096 \
  --rollback_index 1780617600 \
  --chain_partition boot:2:"$COMMON/testkey.avbpubkey" \
  --chain_partition init_boot:3:"$COMMON/testkey.avbpubkey" \
  --chain_partition vbmeta_system:1:"$COMMON/testkey.avbpubkey" \
  --chain_partition vbmeta_vendor_dlkm:4:"$COMMON/testkey.avbpubkey" \
  --chain_partition vbmeta_system_dlkm:5:"$COMMON/testkey.avbpubkey" \
  "${VENDOR_BOOT_DESCRIPTOR_ARGS[@]}" \
  --include_descriptors_from_image "$ODM_IMG" \
  --include_descriptors_from_image "$VENDOR_IMG" \
  --include_descriptors_from_image "$ODM_DLKM_IMG" \
  --output "$COMMON/vbmeta.img"
[ "$failures" -eq 0 ] || exit 1
truncate -s 65536 "$COMMON/vbmeta.img"

cp -a --reflink=auto "$SHARED/system" "$TMP/system_A_src"
rc=$?
if [ "$rc" -ne 0 ]; then fail "copy shared system tree failed rc=$rc"; exit 1; fi

install -m 0644 "$A_BASE/com.android.runtime.apex.baseline" "$TMP/system_A_src/system/apex/com.android.runtime.apex"
rc=$?
if [ "$rc" -ne 0 ]; then fail "install runtime APEX failed rc=$rc"; exit 1; fi
install -m 0644 "$A_BASE/com.android.art.apex.baseline" "$TMP/system_A_src/system/apex/com.android.art.apex"
rc=$?
if [ "$rc" -ne 0 ]; then fail "install ART APEX failed rc=$rc"; exit 1; fi
sha256sum "$TMP/system_A_src/system/apex/com.android.runtime.apex" "$TMP/system_A_src/system/apex/com.android.art.apex" >"$A/input-apex.manifest.txt"

PATH="$ANDROID_ROOT/out/host/linux-x86/bin:$PATH" run_step "build A system.img" "$A/build-system.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/build_image" "$TMP/system_A_src" "$SYSTEM_PROP" "$A/system.img" "$SHARED/system"
[ "$failures" -eq 0 ] || exit 1

run_step "build A vbmeta_system.img" "$A/build-vbmeta-system.log" \
  "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
  --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
  --algorithm SHA256_RSA4096 \
  --padding_size 4096 \
  --rollback_index 1780617600 \
  --include_descriptors_from_image "$PRODUCT_IMG" \
  --include_descriptors_from_image "$A/system.img" \
  --include_descriptors_from_image "$SYSTEM_EXT_IMG" \
  --output "$A/vbmeta_system.img"
[ "$failures" -eq 0 ] || exit 1
truncate -s 65536 "$A/vbmeta_system.img"

build_super_variant A "$A" "$A/system.img"
[ "$failures" -eq 0 ] || exit 1

if [ -n "$B_STABLE_DIR" ]; then
  cp -a --reflink=auto "$SHARED/system" "$TMP/system_B_src"
  rc=$?
  if [ "$rc" -ne 0 ]; then fail "copy shared system tree for B failed rc=$rc"; exit 1; fi

  install -m 0644 "$B_STABLE_DIR/apex/com.android.runtime.apex.B" "$TMP/system_B_src/system/apex/com.android.runtime.apex"
  rc=$?
  if [ "$rc" -ne 0 ]; then fail "install B runtime APEX failed rc=$rc"; exit 1; fi
  install -m 0644 "$B_STABLE_DIR/apex/com.android.art.apex.B" "$TMP/system_B_src/system/apex/com.android.art.apex"
  rc=$?
  if [ "$rc" -ne 0 ]; then fail "install B ART APEX failed rc=$rc"; exit 1; fi
  sha256sum "$TMP/system_B_src/system/apex/com.android.runtime.apex" "$TMP/system_B_src/system/apex/com.android.art.apex" >"$B/input-apex.manifest.txt"

  PATH="$ANDROID_ROOT/out/host/linux-x86/bin:$PATH" run_step "build B system.img from stable APEX" "$B/build-system.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/build_image" "$TMP/system_B_src" "$SYSTEM_PROP" "$B/system.img" "$SHARED/system"
  [ "$failures" -eq 0 ] || exit 1

  run_step "build B vbmeta_system.img" "$B/build-vbmeta-system.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
    --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
    --algorithm SHA256_RSA4096 \
    --padding_size 4096 \
    --rollback_index 1780617600 \
    --include_descriptors_from_image "$PRODUCT_IMG" \
    --include_descriptors_from_image "$B/system.img" \
    --include_descriptors_from_image "$SYSTEM_EXT_IMG" \
    --output "$B/vbmeta_system.img"
  [ "$failures" -eq 0 ] || exit 1
  truncate -s 65536 "$B/vbmeta_system.img"

  build_super_variant B "$B" "$B/system.img"
  [ "$failures" -eq 0 ] || exit 1
else
  ln -s "$PRODUCT_OUT/system.img" "$B/system.img"
  sha256sum "$PRODUCT_OUT/system/apex/com.android.runtime.apex" "$PRODUCT_OUT/system/apex/com.android.art.apex" >"$B/input-apex.manifest.txt"

  run_step "build B vbmeta_system.img" "$B/build-vbmeta-system.log" \
    "$ANDROID_ROOT/out/host/linux-x86/bin/avbtool" make_vbmeta_image \
    --key "$ANDROID_ROOT/external/avb/test/data/testkey_rsa4096.pem" \
    --algorithm SHA256_RSA4096 \
    --padding_size 4096 \
    --rollback_index 1780617600 \
    --include_descriptors_from_image "$PRODUCT_IMG" \
    --include_descriptors_from_image "$PRODUCT_OUT/system.img" \
    --include_descriptors_from_image "$SYSTEM_EXT_IMG" \
    --output "$B/vbmeta_system.img"
  [ "$failures" -eq 0 ] || exit 1
  truncate -s 65536 "$B/vbmeta_system.img"

  build_super_variant B "$B" "$PRODUCT_OUT/system.img"
  [ "$failures" -eq 0 ] || exit 1
fi

sha256sum "$COMMON/vendor_boot.img" "$COMMON/vbmeta.img" "$COMMON/vbmeta_system_dlkm.img" "$COMMON/vbmeta_vendor_dlkm.img" >"$COMMON/manifest.txt"
sha256sum "$A/super.img" "$A/system.img" "$A/vbmeta_system.img" "$COMMON/vendor_boot.img" "$COMMON/vbmeta.img" "$COMMON/vbmeta_system_dlkm.img" "$COMMON/vbmeta_vendor_dlkm.img" >"$A/manifest.txt"
sha256sum "$B/super.img" "$B/system.img" "$B/vbmeta_system.img" "$COMMON/vendor_boot.img" "$COMMON/vbmeta.img" "$COMMON/vbmeta_system_dlkm.img" "$COMMON/vbmeta_vendor_dlkm.img" >"$B/manifest.txt"

PRE_OUT=$WORK_DIR/preflight
B_STABLE_DIR="$B_STABLE_DIR" A_IMAGE_DIR="$A" B_IMAGE_DIR="$B" COMMON_IMAGE_DIR="$COMMON" VENDOR_BOOT_IMAGE="$VENDOR_BOOT_IMAGE" OUT_DIR="$PRE_OUT" \
  "$SCRIPT_DIR/cvd_ab_preflight.sh" >"$WORK_DIR/preflight.stdout" 2>"$WORK_DIR/preflight.stderr"
rc=$?
if [ "$rc" -ne 0 ]; then
  fail "preflight failed rc=$rc; see $PRE_OUT and $WORK_DIR/preflight.stderr"
  cat "$WORK_DIR/preflight.stderr" >&2 || true
  exit 1
fi

note "PASS: composed images in $WORK_DIR"
cat "$A/manifest.txt"
cat "$COMMON/manifest.txt"
