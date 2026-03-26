#!/usr/bin/env bash
set -euo pipefail

QGA_EXEC="/home/nzzhao/learn_os/.agents/tools/qga_exec.py"
TESTS="ext4/001 ext4/003"
PER_TEST_TIMEOUT=240

while [[ $# -gt 0 ]]; do
  case "$1" in
    --qga-exec)
      QGA_EXEC="$2"
      shift 2
      ;;
    --tests)
      TESTS="$2"
      shift 2
      ;;
    --per-test-timeout)
      PER_TEST_TIMEOUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

GUEST_SCRIPT_CONTENT=$(cat <<'GUEST'
#!/usr/bin/env bash
set -euo pipefail

TESTS="__TESTS__"
PER_TEST_TIMEOUT="__PER_TEST_TIMEOUT__"

mkdir -p /var/lib/xfstests/images /mnt/test /mnt/scratch
truncate -s 2G /var/lib/xfstests/images/test.img
truncate -s 2G /var/lib/xfstests/images/scratch.img

umount /mnt/test 2>/dev/null || true
umount /mnt/scratch 2>/dev/null || true
for d in $(losetup -j /var/lib/xfstests/images/test.img | cut -d: -f1); do
  losetup -d "$d" || true
done
for d in $(losetup -j /var/lib/xfstests/images/scratch.img | cut -d: -f1); do
  losetup -d "$d" || true
done

TEST_LOOP=$(losetup --find --show /var/lib/xfstests/images/test.img)
SCRATCH_LOOP=$(losetup --find --show /var/lib/xfstests/images/scratch.img)
mkfs.ext4 -F "$TEST_LOOP" >/tmp/mkfs_test.log 2>&1
mkfs.ext4 -F "$SCRATCH_LOOP" >/tmp/mkfs_scratch.log 2>&1
mount "$TEST_LOOP" /mnt/test

cat >/var/lib/xfstests/local.config <<CFG
export FSTYP=ext4
export TEST_DEV=$TEST_LOOP
export TEST_DIR=/mnt/test
export SCRATCH_DEV=$SCRATCH_LOOP
export SCRATCH_MNT=/mnt/scratch
CFG

cd /var/lib/xfstests
./check -n $TESTS >/tmp/ext4_quick_dry.out 2>/tmp/ext4_quick_dry.err || true

: >/tmp/ext4_quick_summary.txt
for t in $TESTS; do
  echo "== RUN $t ==" | tee -a /tmp/ext4_quick_summary.txt
  if timeout "$PER_TEST_TIMEOUT" ./check "$t" >/tmp/${t//\//_}.out 2>/tmp/${t//\//_}.err; then
    echo "$t PASS" | tee -a /tmp/ext4_quick_summary.txt
  else
    rc=$?
    if [[ $rc -eq 124 ]]; then
      echo "$t TIMEOUT" | tee -a /tmp/ext4_quick_summary.txt
    else
      echo "$t FAIL rc=$rc" | tee -a /tmp/ext4_quick_summary.txt
    fi
  fi
  echo "-- stderr tail --" | tee -a /tmp/ext4_quick_summary.txt
  tail -n 20 /tmp/${t//\//_}.err | tee -a /tmp/ext4_quick_summary.txt || true
  echo "-- stdout tail --" | tee -a /tmp/ext4_quick_summary.txt
  tail -n 40 /tmp/${t//\//_}.out | tee -a /tmp/ext4_quick_summary.txt || true
done

cat /tmp/ext4_quick_summary.txt
GUEST
)

GUEST_SCRIPT_CONTENT="${GUEST_SCRIPT_CONTENT/__TESTS__/$TESTS}"
GUEST_SCRIPT_CONTENT="${GUEST_SCRIPT_CONTENT/__PER_TEST_TIMEOUT__/$PER_TEST_TIMEOUT}"

B64=$(printf '%s' "$GUEST_SCRIPT_CONTENT" | base64 -w0)
python3 "$QGA_EXEC" "echo '$B64' | base64 -d > /tmp/run_ext4_quick_smoke.sh && chmod +x /tmp/run_ext4_quick_smoke.sh"
python3 "$QGA_EXEC" --timeout 7200 '/tmp/run_ext4_quick_smoke.sh'
