#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
adb_preserve_mutable_suspect_file.sh

Immediately preserve a mutable Android /data suspect file before further klog
or triage. The script first creates a root-owned hardlink on the same /data
filesystem to pin the current inode even if the original path is atomically
replaced, then copies the bytes and pulls both metadata and preserved files.

Usage:
  adb_preserve_mutable_suspect_file.sh --serial <SERIAL> --file <DEVICE_PATH> --out <HOST_DIR>

Outputs:
  <HOST_DIR>/device_preserve.stdout.txt
  <HOST_DIR>/device_preserve.stderr.txt
  <HOST_DIR>/preserve_manifest.txt
  <HOST_DIR>/preserved_files.tgz
  <HOST_DIR>/host_sha256sum.txt
EOF
}

SERIAL=""
FILE=""
OUT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --serial) SERIAL="${2:?missing serial}"; shift 2 ;;
    --file) FILE="${2:?missing device path}"; shift 2 ;;
    --out) OUT="${2:?missing host output dir}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$SERIAL" ] || [ -z "$FILE" ] || [ -z "$OUT" ]; then
  usage >&2
  exit 2
fi

mkdir -p "$OUT"

adb -s "$SERIAL" wait-for-device

shell_quote() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

safe_serial="$(printf '%s' "$SERIAL" | tr -c 'A-Za-z0-9_.-' '_')"
unique_id="$(printf '%s_%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$" "$(date +%N 2>/dev/null || printf 0)")"
device_dir="/data/local/tmp/f2fs_preserve_${safe_serial}_${unique_id}"
device_tar="${device_dir}.tgz"

read -r -d '' device_body <<'EOS' || true
set -eu
mkdir -p "$KEEP"
chmod 0700 "$KEEP"
{
  echo "time=$(date +"%F %T")"
  echo "file=$FILE"
  echo "keep=$KEEP"
  stat -c "orig path=%n inode=%i size=%s blocks512=%b mode=%a uid=%u gid=%g" "$FILE"
  ls -lZ "$FILE"
} >"$KEEP/preserve_manifest.txt"
ino=$(stat -c %i "$FILE")
hard="$KEEP/preserved_inode_${ino}.hardlink"
copy="$KEEP/preserved_inode_${ino}.blob"
hard_src=""
if ln "$FILE" "$hard" 2>"$KEEP/hardlink.stderr.txt"; then
  echo "hardlink_status=ok path=$hard" >>"$KEEP/preserve_manifest.txt"
  hard_src="$hard"
else
  echo "hardlink_status=failed path=$hard" >>"$KEEP/preserve_manifest.txt"
  parent="$(dirname "$FILE")"
  parent_hard="$parent/.f2fs_preserved_inode_${ino}_$(date +%s)_$$.hardlink"
  if ln "$FILE" "$parent_hard" 2>"$KEEP/hardlink_parent.stderr.txt"; then
    echo "hardlink_parent_status=ok path=$parent_hard" >>"$KEEP/preserve_manifest.txt"
    hard_src="$parent_hard"
  else
    echo "hardlink_parent_status=failed path=$parent_hard" >>"$KEEP/preserve_manifest.txt"
  fi
fi
if [ -n "$hard_src" ]; then
  if cp -a "$hard_src" "$copy" 2>"$KEEP/copy.stderr.txt"; then
    echo "copy_status=ok source=$hard_src path=$copy" >>"$KEEP/preserve_manifest.txt"
  else
    echo "copy_status=failed_or_partial source=$hard_src path=$copy" >>"$KEEP/preserve_manifest.txt"
  fi
else
  if cp -a "$FILE" "$copy" 2>"$KEEP/copy.stderr.txt"; then
    echo "copy_status=ok source=$FILE path=$copy" >>"$KEEP/preserve_manifest.txt"
  else
    echo "copy_status=failed_or_partial source=$FILE path=$copy" >>"$KEEP/preserve_manifest.txt"
  fi
fi
stat -c "after_orig path=%n inode=%i size=%s blocks512=%b" "$FILE" >>"$KEEP/preserve_manifest.txt" || true
stat -c "hardlink path=%n inode=%i size=%s blocks512=%b" "$hard_src" >>"$KEEP/preserve_manifest.txt" 2>/dev/null || true
stat -c "copy path=%n inode=%i size=%s blocks512=%b" "$copy" >>"$KEEP/preserve_manifest.txt"
orig_size="$(stat -c %s "$FILE" 2>/dev/null || echo unknown)"
copy_size="$(stat -c %s "$copy" 2>/dev/null || echo unknown)"
if [ "$orig_size" = "$copy_size" ]; then
  echo "copy_size_status=full size=$copy_size" >>"$KEEP/preserve_manifest.txt"
else
  echo "copy_size_status=partial_or_short orig_size=$orig_size copy_size=$copy_size" >>"$KEEP/preserve_manifest.txt"
fi
if command -v sha256sum >/dev/null 2>&1; then
  : >"$KEEP/sha256sum.txt"
  sha256sum "$FILE" >>"$KEEP/sha256sum.txt" 2>>"$KEEP/sha256sum.stderr.txt" || echo "sha256_orig_status=failed path=$FILE" >>"$KEEP/preserve_manifest.txt"
  if [ -n "$hard_src" ]; then
    sha256sum "$hard_src" >>"$KEEP/sha256sum.txt" 2>>"$KEEP/sha256sum.stderr.txt" || echo "sha256_hardlink_status=failed path=$hard_src" >>"$KEEP/preserve_manifest.txt"
  fi
  sha256sum "$copy" >>"$KEEP/sha256sum.txt" 2>>"$KEEP/sha256sum.stderr.txt" || echo "sha256_copy_status=failed path=$copy" >>"$KEEP/preserve_manifest.txt"
  echo "sha256_status=ok path=$KEEP/sha256sum.txt" >>"$KEEP/preserve_manifest.txt"
else
  echo "sha256_status=unavailable" >>"$KEEP/preserve_manifest.txt"
fi
cd "$(dirname "$KEEP")"
PACK="${KEEP}.hostpack"
rm -rf "$PACK"
mkdir -p "$PACK"
for item in "$KEEP"/*; do
  case "$item" in
    *.hardlink)
      echo "host_archive_excludes_hardlink=$item" >>"$KEEP/preserve_manifest.txt"
      ;;
  esac
done
for item in "$KEEP"/*; do
  case "$item" in
    *.hardlink)
      ;;
    *)
      cp -a "$item" "$PACK"/ 2>/dev/null || true
      ;;
  esac
done
tar -czf "$TAR" "$(basename "$PACK")"
chmod 0644 "$TAR"
EOS

device_cmd="FILE=$(shell_quote "$FILE") KEEP=$(shell_quote "$device_dir") TAR=$(shell_quote "$device_tar") sh -c $(shell_quote "$device_body")"

adb -s "$SERIAL" shell "su -c $(shell_quote "$device_cmd")" >"$OUT/device_preserve.stdout.txt" 2>"$OUT/device_preserve.stderr.txt"

adb -s "$SERIAL" pull "$device_tar" "$OUT/preserved_files.tgz" >"$OUT/adb_pull.stdout.txt" 2>"$OUT/adb_pull.stderr.txt"
tar -xzf "$OUT/preserved_files.tgz" -C "$OUT"
find "$OUT" -name preserve_manifest.txt -print -exec cp {} "$OUT/preserve_manifest.txt" \;
find "$OUT" -type f \( -name 'preserved_inode_*' -o -name preserve_manifest.txt -o -name sha256sum.txt \) -print0 |
  xargs -0 -r sha256sum >"$OUT/host_sha256sum.txt"

printf 'host_out=%s\n' "$OUT"
printf 'device_keep=%s\n' "$device_dir"
printf 'device_tar=%s\n' "$device_tar"
