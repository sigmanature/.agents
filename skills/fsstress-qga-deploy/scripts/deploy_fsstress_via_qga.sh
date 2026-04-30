#!/usr/bin/env bash
set -euo pipefail

QGA_EXEC="/home/nzzhao/learn_os/.agents/tools/qga_exec.py"
QGA_SOCK="/tmp/qga.sock"
TARGET_DIR="/tmp/fsstress_smoke"
NOPS=100
NPROC=2
SEED=12345
SMOKE_TIMEOUT=20
QGA_TIMEOUT=60

usage() {
  cat <<'USAGE'
Usage:
  deploy_fsstress_via_qga.sh [options]

Options:
  --qga-exec PATH       Host path to qga_exec.py.
  --sock PATH           QGA socket path.
  --target-dir PATH     Guest directory for smoke workload.
  --nops N              fsstress operations per process.
  --nproc N             fsstress process count.
  --seed N              fsstress random seed.
  --timeout SEC         Guest-side smoke timeout.
  --qga-timeout SEC     Host QGA command timeout.
  -h, --help            Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --qga-exec)
      QGA_EXEC="$2"
      shift 2
      ;;
    --sock)
      QGA_SOCK="$2"
      shift 2
      ;;
    --target-dir)
      TARGET_DIR="$2"
      shift 2
      ;;
    --nops)
      NOPS="$2"
      shift 2
      ;;
    --nproc)
      NPROC="$2"
      shift 2
      ;;
    --seed)
      SEED="$2"
      shift 2
      ;;
    --timeout)
      SMOKE_TIMEOUT="$2"
      shift 2
      ;;
    --qga-timeout)
      QGA_TIMEOUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$QGA_EXEC" ]]; then
  echo "qga_exec.py not found: $QGA_EXEC" >&2
  exit 1
fi

if [[ ! -S "$QGA_SOCK" ]]; then
  echo "QGA socket not found: $QGA_SOCK" >&2
  exit 1
fi

GUEST_SCRIPT=$(cat <<'GUEST'
#!/usr/bin/env bash
set -euo pipefail

target_dir="${FSSTRESS_TARGET_DIR:-/tmp/fsstress_smoke}"
nops="${FSSTRESS_NOPS:-100}"
nproc="${FSSTRESS_NPROC:-2}"
seed="${FSSTRESS_SEED:-12345}"
smoke_timeout="${FSSTRESS_TIMEOUT:-20}"
log="/tmp/fsstress_smoke.log"
help_log="/tmp/fsstress_help.txt"

echo "== fsstress deploy start =="
date
uname -a

candidate=""
for path in \
  /usr/local/bin/fsstress \
  /var/lib/xfstests/ltp/fsstress \
  /root/xfstests-dev/ltp/fsstress
do
  if [[ -x "$path" ]]; then
    candidate="$path"
    break
  fi
done

if [[ -z "$candidate" ]]; then
  echo "fsstress binary not found. Run xfstests-qga-ubuntu/scripts/install_xfstests_via_qga.sh first." >&2
  exit 3
fi

if [[ "$candidate" != "/usr/local/bin/fsstress" ]]; then
  ln -sf "$candidate" /usr/local/bin/fsstress
fi

echo "fsstress_path=$(command -v fsstress)"
ls -l /usr/local/bin/fsstress

set +e
fsstress -H >"$help_log" 2>&1
help_rc=$?
set -e
echo "help_rc=$help_rc"
if ! grep -q '^Usage: .*fsstress' "$help_log"; then
  echo "fsstress help did not print usage" >&2
  cat "$help_log" >&2
  exit 4
fi
head -n 8 "$help_log"

rm -rf "$target_dir"
mkdir -p "$target_dir"
findmnt -T "$target_dir" || true

set +e
timeout "${smoke_timeout}s" fsstress \
  -d "$target_dir" \
  -n "$nops" \
  -p "$nproc" \
  -l 1 \
  -c \
  -s "$seed" \
  >"$log" 2>&1
smoke_rc=$?
set -e

echo "smoke_rc=$smoke_rc"
echo "smoke_log=$log"
tail -n 80 "$log" || true
rm -rf "$target_dir"
exit "$smoke_rc"
GUEST
)

B64=$(printf '%s' "$GUEST_SCRIPT" | base64 -w0)

python3 "$QGA_EXEC" --sock "$QGA_SOCK" --timeout "$QGA_TIMEOUT" \
  "echo '$B64' | base64 -d > /tmp/deploy_fsstress_via_qga.sh && chmod +x /tmp/deploy_fsstress_via_qga.sh"

printf -v TARGET_DIR_Q '%q' "$TARGET_DIR"
printf -v NOPS_Q '%q' "$NOPS"
printf -v NPROC_Q '%q' "$NPROC"
printf -v SEED_Q '%q' "$SEED"
printf -v SMOKE_TIMEOUT_Q '%q' "$SMOKE_TIMEOUT"

python3 "$QGA_EXEC" --sock "$QGA_SOCK" --timeout "$QGA_TIMEOUT" \
  "FSSTRESS_TARGET_DIR=$TARGET_DIR_Q FSSTRESS_NOPS=$NOPS_Q FSSTRESS_NPROC=$NPROC_Q FSSTRESS_SEED=$SEED_Q FSSTRESS_TIMEOUT=$SMOKE_TIMEOUT_Q /tmp/deploy_fsstress_via_qga.sh"
