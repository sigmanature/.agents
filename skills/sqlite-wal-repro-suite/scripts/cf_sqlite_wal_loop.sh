#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
SERIAL=""
SECONDS=120
SLEEP_MS=20
DB="/data/local/tmp/cf_wal_repro.db"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    --seconds) SECONDS="$2"; shift 2;;
    --sleep-ms) SLEEP_MS="$2"; shift 2;;
    --db) DB="$2"; shift 2;;
    -h|--help) echo "Usage: cf_sqlite_wal_loop.sh --run-dir RUN_DIR [--seconds N] [--db PATH]"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/adb" ]] || { echo "invalid --run-dir" >&2; exit 1; }
ADB=("$RUN_DIR/bin/adb")
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )

"${ADB[@]}" shell 'command -v sqlite3 >/dev/null || { echo sqlite3_missing; exit 127; }'
"${ADB[@]}" shell "rm -f '$DB' '$DB-wal' '$DB-shm'; sqlite3 '$DB' 'PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS t(k INTEGER PRIMARY KEY, v TEXT);'"
"${ADB[@]}" shell "end=\$((\$(date +%s)+$SECONDS)); i=0; while [ \$(date +%s) -lt \$end ]; do i=\$((i+1)); sqlite3 '$DB' \"BEGIN IMMEDIATE; INSERT INTO t(v) VALUES('v-'||randomblob(64)); COMMIT; PRAGMA wal_checkpoint(TRUNCATE);\" || exit \$?; sleep $(awk "BEGIN {print $SLEEP_MS/1000}"); done; echo loops=\$i; ls -l '$DB'*"
