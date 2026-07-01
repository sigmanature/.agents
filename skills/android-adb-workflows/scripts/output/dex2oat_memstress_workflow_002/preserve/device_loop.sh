#!/system/bin/sh
set -eu

KEEP="/data/local/tmp/preserve_18281FDF6007HB_20260701_221923_3679090_559395844"
TARGETS="/data/local/tmp/preserve_18281FDF6007HB_20260701_221923_3679090_559395844/targets.txt"
PID_FILE="/data/local/tmp/preserve_18281FDF6007HB_20260701_221923_3679090_559395844/pid"
INTERVAL="2.0"
KEEP_COUNT="2"
WATCH_TOMBSTONE="1"
LAST_TOMBSTONE=""

echo "$$" > "$PID_FILE"

# Try to make this watcher as hard to OOM-kill as possible.
echo -1000 > /proc/self/oom_score_adj 2>/dev/null || true
renice 19 "$$" 2>/dev/null || true
ionice -c 3 -n 7 -p "$$" 2>/dev/null || true

log() {
  echo "$(date +'%F %T') $1" >> "/data/local/tmp/preserve_18281FDF6007HB_20260701_221923_3679090_559395844/loop.log"
}

log "watcher started pid=$$ keep=$KEEP interval=$INTERVAL keep_count=$KEEP_COUNT watch_tombstone=$WATCH_TOMBSTONE"

# If watching tombstones, remember the newest one at startup so we only react to new ones.
if [ "$WATCH_TOMBSTONE" = "1" ]; then
  LAST_TOMBSTONE=$(ls -t /data/tombstones/ 2>/dev/null | head -1)
fi

while :; do
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -e "$f" ] || continue
    ino=$(stat -c %i "$f" 2>/dev/null || echo 0)
    [ "$ino" -ne 0 ] || continue
    base=$(basename "$f" | tr -c 'A-Za-z0-9_.-' '_')
    hard="$KEEP/${base}_${ino}.hardlink"
    if [ ! -e "$hard" ]; then
      if ln "$f" "$hard" 2>/dev/null; then
        log "hardlinked file=$f inode=$ino hard=$hard"
      fi
    fi
  done < "$TARGETS"

  # Trim older versions, keeping only the latest KEEP_COUNT per watched path.
  if [ "$KEEP_COUNT" -gt 0 ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      base=$(basename "$f" | tr -c 'A-Za-z0-9_.-' '_')
      pattern="$KEEP/${base}"_*.hardlink
      ls -t $pattern 2>/dev/null | awk "NR > $KEEP_COUNT" | while IFS= read -r old; do
        [ -e "$old" ] || continue
        rm -f "$old"
        log "trimmed old=$old"
      done
    done < "$TARGETS"
  fi

  # Optional tombstone detection: if a new tombstone appears, immediately
  # hardlink any .odex/.vdex it references, record details, and exit so the host
  # can pull preserved artifacts and stop the stress test.
  if [ "$WATCH_TOMBSTONE" = "1" ]; then
    current=$(ls -t /data/tombstones/ 2>/dev/null | head -1)
    if [ -n "$current" ] && [ "$current" != "$LAST_TOMBSTONE" ]; then
      LAST_TOMBSTONE="$current"
      tpath="/data/tombstones/$current"
      pkg=$(grep -m1 '^Cmdline:' "$tpath" 2>/dev/null | sed 's/^Cmdline: //')
      artifacts=$(grep -oE '/data/app/[^ ]+\.(odex|vdex)' "$tpath" 2>/dev/null | sort -u)
      {
        echo "tombstone=$tpath"
        echo "timestamp=$(date +'%F %T')"
        echo "package=$pkg"
        echo "artifacts:"
        echo "$artifacts"
      } > "$KEEP/tombstone_triggered.txt"
      log "tombstone_detected file=$tpath package=$pkg"
      echo "$artifacts" | while IFS= read -r af; do
        [ -n "$af" ] || continue
        [ -e "$af" ] || continue
        aino=$(stat -c %i "$af" 2>/dev/null || echo 0)
        [ "$aino" -ne 0 ] || continue
        abase=$(basename "$af" | tr -c 'A-Za-z0-9_.-' '_')
        if ln "$af" "$KEEP/tombstone_${abase}_${aino}.hardlink" 2>/dev/null; then
          log "tombstone_hardlinked file=$af inode=$aino"
        fi
      done
      log "watcher exiting due to tombstone"
      exit 0
    fi
  fi

  sleep "$INTERVAL"
done
