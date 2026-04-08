#!/usr/bin/env bash
set -euo pipefail

SERIAL=""
PHASE="write"   # write | verify
DIR="/data/media/0/Download"
FILE_BASENAME="lf.c"

# default: 5MiB + 123 (intentionally not 4K-aligned)
OFF1=$((5 * 1024 * 1024 + 123))
LEN1=3000
LEN2=1234

usage() {
  cat <<'EOF'
Usage:
  lf_unaligned_rw_smoke.sh --serial <SERIAL> [--phase write|verify] [--dir <DIR>] [--file <NAME>]

What it tests (on-device, root required):
  - Encrypted dir file persistence with large folio enabled
  - Read-then-write path (readahead + pagecache populated)
  - Unaligned overwrite at a non-4K offset and non-4K length
  - Unaligned append
  - sync + drop_caches + readback region hash verification

Defaults:
  DIR=/data/media/0/Download
  FILE=lf.c
  overwrite offset = 5MiB+123, length=3000
  append length=1234
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="${2:-}"; shift 2;;
    --phase) PHASE="${2:-}"; shift 2;;
    --dir) DIR="${2:-}"; shift 2;;
    --file) FILE_BASENAME="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$SERIAL" ]]; then
  echo "missing --serial" >&2
  usage
  exit 2
fi

ADB=(adb -s "$SERIAL")

TARGET="${DIR%/}/${FILE_BASENAME}"
PAY1="${DIR%/}/.${FILE_BASENAME}.payload1.bin"
PAY2="${DIR%/}/.${FILE_BASENAME}.payload2.bin"
META="${DIR%/}/.${FILE_BASENAME}.meta"

if [[ "$PHASE" != "write" && "$PHASE" != "verify" ]]; then
  echo "bad --phase: $PHASE (want write|verify)" >&2
  exit 2
fi

echo "[*] serial=$SERIAL phase=$PHASE target=$TARGET" >&2

# --- phase write: create + read + unaligned overwrite + unaligned append + verify ---
if [[ "$PHASE" == "write" ]]; then
  "${ADB[@]}" shell su -c "sh -c '
set -eu
(set -o pipefail) 2>/dev/null || true

DIR=\"${DIR%/}\"
TARGET=\"$TARGET\"
PAY1=\"$PAY1\"
PAY2=\"$PAY2\"
META=\"$META\"

OFF1=$OFF1
LEN1=$LEN1
LEN2=$LEN2

sha() { command -v sha256sum >/dev/null 2>&1 && sha256sum \"\$@\" || toybox sha256sum \"\$@\"; }
fsize() { stat -c %s \"\$1\" 2>/dev/null || wc -c < \"\$1\"; }

mkdir -p \"\$DIR\"
cd \"\$DIR\"

echo \"[*] lsattr dir:\" >&2
lsattr -d \"\$DIR\" >&2 || true

if [ ! -e \"\$TARGET\" ]; then
  echo \"[*] create 10MiB zeros: \$TARGET\" >&2
  dd if=/dev/zero of=\"\$TARGET\" bs=1M count=10 status=none
  sync
fi

echo \"[*] lsattr file:\" >&2
lsattr \"\$TARGET\" >&2 || true
echo \"[*] size=\$(fsize \"\$TARGET\")\" >&2

echo \"[*] warm read (trigger readahead / folio promotion): dd -> /dev/null\" >&2
dd if=\"\$TARGET\" of=/dev/null bs=256K status=none

echo \"[*] build overwrite payload LEN1=\$LEN1 -> \$PAY1\" >&2
dd if=/dev/urandom of=\"\$PAY1\" bs=1 count=\"\$LEN1\" status=none
sync

echo \"[*] unaligned overwrite at OFF1=\$OFF1 (bs=1 seek bytes)\" >&2
dd if=\"\$PAY1\" of=\"\$TARGET\" bs=1 seek=\"\$OFF1\" conv=notrunc status=none
sync

echo \"[*] build append payload LEN2=\$LEN2 -> \$PAY2\" >&2
dd if=/dev/urandom of=\"\$PAY2\" bs=1 count=\"\$LEN2\" status=none
sync

OLD_SIZE=\$(fsize \"\$TARGET\")
echo \"[*] unaligned append at old_size=\$OLD_SIZE\" >&2
dd if=\"\$PAY2\" of=\"\$TARGET\" bs=1 seek=\"\$OLD_SIZE\" conv=notrunc status=none
sync

NEW_SIZE=\$(fsize \"\$TARGET\")
echo \"[*] new_size=\$NEW_SIZE\" >&2

echo \"OFF1=\$OFF1\" > \"\$META\"
echo \"LEN1=\$LEN1\" >> \"\$META\"
echo \"APP_OFF=\$OLD_SIZE\" >> \"\$META\"
echo \"LEN2=\$LEN2\" >> \"\$META\"
chmod 0644 \"\$PAY1\" \"\$PAY2\" \"\$META\" || true

echo \"[*] drop_caches + verify hashes\" >&2
echo 3 > /proc/sys/vm/drop_caches

PAY1_H=\$(sha \"\$PAY1\" | awk \"{print \\\$1}\")
R1_H=\$(dd if=\"\$TARGET\" bs=1 skip=\"\$OFF1\" count=\"\$LEN1\" status=none | sha | awk \"{print \\\$1}\")
test \"\$PAY1_H\" = \"\$R1_H\" && echo \"[OK] overwrite region hash match\" >&2 || { echo \"[FAIL] overwrite region hash mismatch\" >&2; exit 1; }

APP_OFF=\$(awk -F= \"\\\$1==\\\"APP_OFF\\\" {print \\\$2}\" \"\$META\")
PAY2_H=\$(sha \"\$PAY2\" | awk \"{print \\\$1}\")
R2_H=\$(dd if=\"\$TARGET\" bs=1 skip=\"\$APP_OFF\" count=\"\$LEN2\" status=none | sha | awk \"{print \\\$1}\")
test \"\$PAY2_H\" = \"\$R2_H\" && echo \"[OK] append region hash match\" >&2 || { echo \"[FAIL] append region hash mismatch\" >&2; exit 1; }

echo \"[*] tail dmesg hints\" >&2
dmesg | tail -n 120 | grep -E \"f2fs|F2FS|fscrypt|inline|crypto|EINVAL|EIO|BUG|WARN\" >&2 || true

echo \"[OK] write phase done\" >&2
'"
fi

# --- phase verify: drop_caches + verify only (use existing payloads/meta) ---
if [[ "$PHASE" == "verify" ]]; then
  "${ADB[@]}" shell su -c "sh -c '
set -eu
(set -o pipefail) 2>/dev/null || true

TARGET=\"$TARGET\"
PAY1=\"$PAY1\"
PAY2=\"$PAY2\"
META=\"$META\"

sha() { command -v sha256sum >/dev/null 2>&1 && sha256sum \"\$@\" || toybox sha256sum \"\$@\"; }

if [ ! -e \"\$TARGET\" ]; then
  echo \"[FAIL] missing target: \$TARGET\" >&2
  exit 1
fi
if [ ! -e \"\$PAY1\" ] || [ ! -e \"\$PAY2\" ] || [ ! -e \"\$META\" ]; then
  echo \"[FAIL] missing payload/meta (need \$PAY1 \$PAY2 \$META)\" >&2
  exit 1
fi

OFF1=\$(awk -F= \"\\\$1==\\\"OFF1\\\" {print \\\$2}\" \"\$META\")
LEN1=\$(awk -F= \"\\\$1==\\\"LEN1\\\" {print \\\$2}\" \"\$META\")
APP_OFF=\$(awk -F= \"\\\$1==\\\"APP_OFF\\\" {print \\\$2}\" \"\$META\")
LEN2=\$(awk -F= \"\\\$1==\\\"LEN2\\\" {print \\\$2}\" \"\$META\")

echo \"[*] verify: drop_caches then compare region hashes\" >&2
echo 3 > /proc/sys/vm/drop_caches

PAY1_H=\$(sha \"\$PAY1\" | awk \"{print \\\$1}\")
R1_H=\$(dd if=\"\$TARGET\" bs=1 skip=\"\$OFF1\" count=\"\$LEN1\" status=none | sha | awk \"{print \\\$1}\")
test \"\$PAY1_H\" = \"\$R1_H\" && echo \"[OK] overwrite region hash match\" >&2 || { echo \"[FAIL] overwrite region hash mismatch\" >&2; exit 1; }

PAY2_H=\$(sha \"\$PAY2\" | awk \"{print \\\$1}\")
R2_H=\$(dd if=\"\$TARGET\" bs=1 skip=\"\$APP_OFF\" count=\"\$LEN2\" status=none | sha | awk \"{print \\\$1}\")
test \"\$PAY2_H\" = \"\$R2_H\" && echo \"[OK] append region hash match\" >&2 || { echo \"[FAIL] append region hash mismatch\" >&2; exit 1; }

echo \"[*] tail dmesg hints\" >&2
dmesg | tail -n 120 | grep -E \"f2fs|F2FS|fscrypt|inline|crypto|EINVAL|EIO|BUG|WARN\" >&2 || true

echo \"[OK] verify phase done\" >&2
'"
fi
