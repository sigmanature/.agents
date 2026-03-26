#!/usr/bin/env bash
set -euo pipefail

# Host-side launcher: install xfstests inside guest via QGA only (no SSH)
# Usage:
#   scripts/install_xfstests_via_qga.sh [--qga-exec /abs/path/qga_exec.py] [--timeout 10800]

QGA_EXEC="/home/nzzhao/learn_os/.agents/tools/qga_exec.py"
TIMEOUT=10800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --qga-exec)
      QGA_EXEC="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$QGA_EXEC" ]]; then
  echo "qga_exec.py not found: $QGA_EXEC" >&2
  exit 1
fi

GUEST_SCRIPT_CONTENT=$(cat <<'GUEST'
#!/usr/bin/env bash
set -euo pipefail

LOG=/tmp/xfstests_install_full.log
LOCK=/tmp/xfstests_install_full.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "installer already running: $LOCK"
  exit 0
fi

exec > >(tee -a "$LOG") 2>&1

echo "== xfstests full install start =="
date
uname -a
cd /root

wait_apt() {
  while ps -eo comm | grep -qE '^(apt-get|dpkg)$'; do
    ps -eo pid,etime,stat,cmd | grep -E '(apt-get|dpkg)' | grep -v grep || true
    sleep 3
  done
}

wait_apt
DEBIAN_FRONTEND=noninteractive apt-get update -y
wait_apt
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git ca-certificates bc \
  build-essential make autoconf automake libtool pkg-config gettext bison flex \
  uuid-dev libaio-dev libattr1-dev libacl1-dev libgdbm-dev libdb-dev libtirpc-dev libblkid-dev libreadline-dev \
  xfsprogs quota attr acl xfslibs-dev e2fsprogs

# Reset potentially polluted xfs headers to distro state.
rm -f /usr/include/xfs/*.h || true
DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y xfslibs-dev

# Quick header probe for distro-provided xfs userspace APIs needed by check scripts.
printf '#include <xfs/xfs.h>\n#include <xfs/xqm.h>\n#include <xfs/handle.h>\n' \
  | gcc -x c - -c -o /tmp/_xfs_hdr_test.o >/dev/null 2>&1

echo "== clone/update xfstests-dev =="
if [[ -d /root/xfstests-dev/.git ]]; then
  git -C /root/xfstests-dev fetch --depth 1 origin || true
  git -C /root/xfstests-dev status -sb || true
else
  if ! git clone --depth 1 https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git /root/xfstests-dev; then
    git clone --depth 1 https://github.com/kdave/xfstests.git /root/xfstests-dev
  fi
fi

cd /root/xfstests-dev

# Compatibility patch for some trees on Ubuntu userspace headers.
if [[ -f src/vfs/missing.h ]] && ! grep -q '^#include <linux/mount.h>$' src/vfs/missing.h; then
  sed -i '/^#include <linux\/types.h>$/a #include <linux/mount.h>' src/vfs/missing.h
fi

if [[ ! -x ./configure ]]; then
  if [[ -x ./autogen.sh ]]; then
    ./autogen.sh
  else
    autoreconf -fi
  fi
fi

./configure --libexecdir=/usr/lib --exec_prefix=/var/lib
make -j"$(nproc)"

# Some source trees keep install-sh only under include/ but install rules refer to ../install-sh.
if [[ ! -x ./install-sh ]] && [[ -x ./include/install-sh ]]; then
  cp ./include/install-sh ./install-sh
  chmod +x ./install-sh
fi

make install

# Ensure check is usable from any cwd.
install -d -m 0755 /var/lib/xfstests
install -m 0755 /root/xfstests-dev/check /var/lib/xfstests/check
cat >/usr/local/bin/check <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
cd /var/lib/xfstests
exec ./check "$@"
WRAP
chmod 0755 /usr/local/bin/check

/usr/local/bin/check -h >/tmp/check_h.out 2>/tmp/check_h.err || true
if ! grep -q '^Usage:' /tmp/check_h.out; then
  echo "check -h did not print usage" >&2
  echo "---- stderr ----"
  cat /tmp/check_h.err || true
  echo "---- stdout ----"
  cat /tmp/check_h.out || true
  exit 1
fi

echo "== install complete =="
command -v check || true
ls -l /usr/local/bin/check /var/lib/xfstests/check || true
date
GUEST
)

B64=$(printf '%s' "$GUEST_SCRIPT_CONTENT" | base64 -w0)

python3 "$QGA_EXEC" "echo '$B64' | base64 -d > /tmp/install_xfstests_full.sh && chmod +x /tmp/install_xfstests_full.sh"
python3 "$QGA_EXEC" --timeout "$TIMEOUT" '/tmp/install_xfstests_full.sh'

echo
echo "Guest install finished. Validate quickly with:"
echo "  python3 $QGA_EXEC '/usr/local/bin/check -h | head -n 20'"
