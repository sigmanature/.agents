#!/usr/bin/env bash
set -u
export LC_ALL=C

TESTDIR=${TESTDIR:-"$HOME/fio-f2fs-test"}
SIZE=${SIZE:-1G}
REPEAT=${REPEAT:-3}
COOLDOWN=${COOLDOWN:-5}

RW_MODE=${RW_MODE:-seq}
FIO_RW_READ="read"
FIO_RW_WRITE="write"
if [ "$RW_MODE" = "random" ]; then
  FIO_RW_READ="randread"
  FIO_RW_WRITE="randwrite"
  NORANDOMMAP=1
fi

F2FS_DEV=${F2FS_DEV:-dm-49}
RUN_READ=${RUN_READ:-1}
RUN_WRITE=${RUN_WRITE:-1}
RUN_CACHE_CASES=${RUN_CACHE_CASES:-auto}
FIO_ENGINE=${FIO_ENGINE:-psync}
BS_LIST=(4k 64k 1M)

HEADER_SUMMARY=$'phase\ttype\torder\tbatch_read\tskip_ffs\tO\tS\tW\tbs\trep\trw\tsize\tiops\tbw_MiB_s\tlat_mean_us\tjson'
MASTER_ROW_FIELDS=(phase type O S W bs rw size)

has_root() {
  command -v su >/dev/null 2>&1 || return 1
  [ "$(su -c 'id -u' 2>/dev/null | tr -d '\r')" = "0" ]
}

ROOT=0
if has_root; then
  ROOT=1
fi

root_sh() {
  if [ "$ROOT" = "1" ]; then
    su -c "$*"
  else
    return 1
  fi
}

read_sysfs_value() {
  local path="$1"
  local fallback="$2"
  local val

  val="$(su -c "cat $path" 2>/dev/null | tr -d '\r' || true)"
  if [ -n "$val" ]; then
    printf '%s\n' "$val"
  else
    printf '%s\n' "$fallback"
  fi
}

current_order() {
  read_sysfs_value "/sys/fs/f2fs/$F2FS_DEV/max_folio_order_cap" "unknown"
}

current_batch() {
  read_sysfs_value "/sys/fs/f2fs/$F2FS_DEV/batch_read_pages_pending" "NA"
}

current_skip() {
  read_sysfs_value "/sys/fs/f2fs/$F2FS_DEV/skip_ffs_for_whole_bio" "NA"
}

build_profile_specs() {
  if [ -n "${ORDER:-}" ] || [ -n "${BATCH_READ:-}" ] || [ -n "${SKIP_FFS:-}" ]; then
    local disp_order disp_batch disp_skip

    disp_order="${ORDER:-$(current_order)}"
    disp_batch="${BATCH_READ:-$(current_batch)}"
    disp_skip="${SKIP_FFS:-$(current_skip)}"

    PROFILE_SPECS="$(printf '%s|%s|%s|%s|%s|%s\n' \
      "$disp_order" "$disp_batch" "$disp_skip" \
      "$disp_order" "$disp_batch" "$disp_skip")"
  else
    PROFILE_SPECS="$(cat <<'EOF'
0|-|-|0|0|0
2|0|0|2|0|0
2|1|0|2|1|0
2|1|1|2|1|1
EOF
)"
  fi
}

count_profiles() {
  printf '%s\n' "$PROFILE_SPECS" | sed '/^$/d' | wc -l | tr -d ' '
}

profile_label() {
  local disp_order="$1"
  local disp_batch="$2"
  local disp_skip="$3"
  printf 'order=%s,batch=%s,skip=%s\n' "$disp_order" "$disp_batch" "$disp_skip"
}

write_summary_header() {
  local path="$1"
  printf '%s\n' "$HEADER_SUMMARY" > "$path"
}

log() {
  local line
  line="$(date '+%F %T') $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$MASTER_LOG"
  if [ -n "${CURRENT_LOG:-}" ]; then
    printf '%s\n' "$line" >> "$CURRENT_LOG"
  fi
}

save_original_dirty() {
  : > "$DIRTY_SAVE"
  [ "$ROOT" = "1" ] || return 0

  for k in dirty_writeback_centisecs dirty_expire_centisecs dirty_background_ratio dirty_ratio; do
    local v
    v="$(root_sh "cat /proc/sys/vm/$k" 2>/dev/null || true)"
    if [ -n "$v" ]; then
      printf '%s=%s\n' "$k" "$v" >> "$DIRTY_SAVE"
    fi
  done
}

restore_dirty() {
  [ "$ROOT" = "1" ] || return 0
  [ -f "$DIRTY_SAVE" ] || return 0

  while IFS='=' read -r k v; do
    [ -n "${k:-}" ] || continue
    [ -n "${v:-}" ] || continue
    root_sh "echo $v > /proc/sys/vm/$k" >/dev/null 2>&1 || true
  done < "$DIRTY_SAVE"
}

capture_original_sysfs() {
  [ "$ROOT" = "1" ] || return 0
  OLD_ORDER="$(root_sh "cat /sys/fs/f2fs/$F2FS_DEV/max_folio_order_cap" 2>/dev/null || echo 2)"
  OLD_BATCH="$(root_sh "cat /sys/fs/f2fs/$F2FS_DEV/batch_read_pages_pending" 2>/dev/null || echo 0)"
  OLD_SKIP="$(root_sh "cat /sys/fs/f2fs/$F2FS_DEV/skip_ffs_for_whole_bio" 2>/dev/null || echo 1)"
}

apply_profile_sysfs() {
  if [ "$ROOT" != "1" ]; then
    log "WARNING: no root, cannot apply profile sysfs knobs; profile comparison is not meaningful"
    return 0
  fi

  root_sh "echo $APPLY_ORDER > /sys/fs/f2fs/$F2FS_DEV/max_folio_order_cap" >/dev/null 2>&1 || true
  root_sh "echo $APPLY_BATCH > /sys/fs/f2fs/$F2FS_DEV/batch_read_pages_pending" >/dev/null 2>&1 || true
  root_sh "echo $APPLY_SKIP > /sys/fs/f2fs/$F2FS_DEV/skip_ffs_for_whole_bio" >/dev/null 2>&1 || true
  log "sysfs set: display=$PROFILE_LABEL apply(order=$APPLY_ORDER batch_read=$APPLY_BATCH skip_ffs=$APPLY_SKIP)"
}

restore_sysfs() {
  [ "$ROOT" = "1" ] || return 0
  root_sh "echo ${OLD_ORDER:-2} > /sys/fs/f2fs/$F2FS_DEV/max_folio_order_cap" >/dev/null 2>&1 || true
  root_sh "echo ${OLD_BATCH:-0} > /sys/fs/f2fs/$F2FS_DEV/batch_read_pages_pending" >/dev/null 2>&1 || true
  root_sh "echo ${OLD_SKIP:-1} > /sys/fs/f2fs/$F2FS_DEV/skip_ffs_for_whole_bio" >/dev/null 2>&1 || true
}

cleanup() {
  restore_dirty
  restore_sysfs
  termux-wake-unlock >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

save_profile_metadata() {
  {
    echo "date: $(date)"
    echo "root: $ROOT"
    echo "profile_label: $PROFILE_LABEL"
    echo "display_order: $DISPLAY_ORDER"
    echo "display_batch_read: $DISPLAY_BATCH"
    echo "display_skip_ffs: $DISPLAY_SKIP"
    echo "apply_order: $APPLY_ORDER"
    echo "apply_batch_read: $APPLY_BATCH"
    echo "apply_skip_ffs: $APPLY_SKIP"
    echo "testdir: $TESTDIR"
    echo "size: $SIZE"
    echo "repeat: $REPEAT"
    echo "fio_engine: $FIO_ENGINE"
    echo "large_folio_max_order: $DISPLAY_ORDER"
    echo "batch_read_pages_pending: $DISPLAY_BATCH"
    echo "skip_ffs_for_whole_bio: $DISPLAY_SKIP"
    echo "rw_mode: $RW_MODE"
    echo
    uname -a 2>/dev/null || true
    command -v getprop >/dev/null 2>&1 && getprop ro.product.model || true
    command -v getprop >/dev/null 2>&1 && getprop ro.build.fingerprint || true
    echo
    fio --version 2>/dev/null || true
    echo
    df -h "$TESTDIR" 2>&1 || true
    echo
    stat -f -c 'fstype=%T bsize=%s blocks=%b free=%f' "$TESTDIR" 2>&1 || true
    echo
    mount 2>/dev/null | grep ' /data ' || true
  } > "$CURRENT_OUTROOT/metadata.txt"

  TESTDIR_ABS="$(cd "$TESTDIR" && pwd -P)"
  case "$TESTDIR_ABS" in
    /data/*)
      log "OK: TESTDIR is under /data: $TESTDIR_ABS"
      ;;
    *)
      log "WARNING: TESTDIR is not under /data: $TESTDIR_ABS"
      ;;
  esac

  fstype="$(stat -f -c %T "$TESTDIR" 2>/dev/null || echo unknown)"
  if [ "$fstype" != "f2fs" ]; then
    log "WARNING: filesystem type is $fstype, not f2fs"
  else
    log "OK: filesystem type is f2fs"
  fi
}

DROP_WARNED=0
drop_caches() {
  sync
  if [ "$ROOT" = "1" ]; then
    root_sh "echo 3 > /proc/sys/vm/drop_caches" >/dev/null 2>&1 || log "WARNING: drop_caches failed"
  else
    if [ "$DROP_WARNED" = "0" ]; then
      log "WARNING: no root, cannot drop page cache. Read results may be hot-cache results."
      DROP_WARNED=1
    fi
  fi
  sleep 1
}

set_cache_dirty() {
  [ "$ROOT" = "1" ] || return 1
  root_sh "echo 0 > /proc/sys/vm/dirty_writeback_centisecs" >/dev/null 2>&1 || true
  root_sh "echo 30000 > /proc/sys/vm/dirty_expire_centisecs" >/dev/null 2>&1 || true
  root_sh "echo 100 > /proc/sys/vm/dirty_background_ratio" >/dev/null 2>&1 || true
  root_sh "echo 100 > /proc/sys/vm/dirty_ratio" >/dev/null 2>&1 || true
}

prepare_hole_file() {
  local file="$1"
  rm -f "$file"
  drop_caches
  truncate -s "$SIZE" "$file" || exit 1
  sync
  drop_caches
}

prepare_data_file() {
  local file="$1"
  local prep_log="$CURRENT_OUTROOT/prep_$(basename "$file").log"

  rm -f "$file"
  drop_caches

  log "prepare data file: $file"

  fio \
    --name=prepare \
    --filename="$file" \
    --direct=0 \
    --iodepth=1 \
    --rw=write \
    --bs=1M \
    --ioengine="$FIO_ENGINE" \
    --size="$SIZE" \
    --numjobs=1 \
    --fallocate=none \
    --end_fsync=1 \
    --group_reporting \
    > "$prep_log" || exit 1

  sync
  drop_caches
}

parse_json() {
  local json="$1"
  local phase="$2"
  local typ="$3"
  local o="$4"
  local s="$5"
  local w="$6"
  local bs="$7"
  local rep="$8"
  local rw="$9"

  [ -n "$PYTHON_BIN" ] || return 0

  "$PYTHON_BIN" - \
    "$json" \
    "$CURRENT_SUMMARY" \
    "$MASTER_SUMMARY" \
    "$phase" \
    "$typ" \
    "$DISPLAY_ORDER" \
    "$DISPLAY_BATCH" \
    "$DISPLAY_SKIP" \
    "$o" \
    "$s" \
    "$w" \
    "$bs" \
    "$rep" \
    "$rw" \
    "$SIZE" <<'PY'
import json
import sys

(
    json_path,
    profile_summary,
    master_summary,
    phase,
    typ,
    order,
    batch_read,
    skip_ffs,
    O,
    S,
    W,
    bs,
    rep,
    rw,
    size,
) = sys.argv[1:]

with open(json_path, "r") as f:
    data = json.load(f)

job = data["jobs"][0]
op = "read" if "read" in rw else "write"
st = job.get(op, {})

iops = float(st.get("iops", 0.0) or 0.0)
bw_bytes = st.get("bw_bytes", None)
if bw_bytes is None:
    bw_bytes = float(st.get("bw", 0.0) or 0.0) * 1024.0
bw_mib = float(bw_bytes) / 1048576.0

lat_us = ""
for key in ("clat_ns", "lat_ns"):
    val = st.get(key)
    if isinstance(val, dict) and val.get("mean") is not None:
        lat_us = "%.3f" % (float(val["mean"]) / 1000.0)
        break

line = (
    f"{phase}\t{typ}\t{order}\t{batch_read}\t{skip_ffs}\t"
    f"{O}\t{S}\t{W}\t{bs}\t{rep}\t{rw}\t"
    f"{size}\t{iops:.3f}\t{bw_mib:.3f}\t{lat_us}\t{json_path}\n"
)

for target in (profile_summary, master_summary):
    with open(target, "a") as out:
        out.write(line)
PY
}

run_fio() {
  local phase="$1"
  local typ="$2"
  local o="$3"
  local s="$4"
  local w="$5"
  local bs="$6"
  local rep="$7"
  local rw="$8"
  local fsync_n="$9"
  local overwrite="${10}"
  local file="${11}"

  local name="${phase}_${typ}_O${o}_S${s}_W${w}_${bs}_rep${rep}"
  local json="$CURRENT_JSONDIR/$name.json"

  local args=(
    "--name=$name"
    "--filename=$file"
    "--direct=0"
    "--iodepth=1"
    "--fsync=$fsync_n"
    "--rw=$rw"
    "--numjobs=1"
    "--bs=$bs"
    "--ioengine=$FIO_ENGINE"
    "--size=$SIZE"
    "--norandommap=${NORANDOMMAP:-0}"
    "--fallocate=none"
    "--overwrite=$overwrite"
    "--group_reporting"
    "--output-format=json"
  )

  log "fio: $PROFILE_LABEL :: $name"
  fio "${args[@]}" > "$json" || exit 1

  parse_json "$json" "$phase" "$typ" "$o" "$s" "$w" "$bs" "$rep" "$rw"
  sleep "$COOLDOWN"
}

run_reads() {
  log "=== READ TESTS :: $PROFILE_LABEL ==="

  for rep in $(seq 1 "$REPEAT"); do
    for bs in "${BS_LIST[@]}"; do
      local file="$TESTDIR/read_hole_${bs}_rep${rep}.dat"
      prepare_hole_file "$file"
      run_fio read hole - - - "$bs" "$rep" "$FIO_RW_READ" 0 0 "$file"
      rm -f "$file"
      drop_caches
    done

    for bs in "${BS_LIST[@]}"; do
      local file="$TESTDIR/read_f2fs_${bs}_rep${rep}.dat"
      prepare_data_file "$file"
      run_fio read f2fs - - - "$bs" "$rep" "$FIO_RW_READ" 0 0 "$file"
      rm -f "$file"
      drop_caches
    done
  done
}

run_write_case() {
  local typ="$1"
  local o="$2"
  local s="$3"
  local w="$4"
  local bs="$5"
  local rep="$6"
  local fsync_n="$7"
  local overwrite="$8"
  local file="$TESTDIR/write_${typ}_O${o}_S${s}_W${w}_${bs}_rep${rep}.dat"

  if [ "$overwrite" = "1" ]; then
    prepare_data_file "$file"
  else
    rm -f "$file"
    drop_caches
  fi

  run_fio write "$typ" "$o" "$s" "$w" "$bs" "$rep" "$FIO_RW_WRITE" "$fsync_n" "$overwrite" "$file"

  rm -f "$file"
  drop_caches
}

run_cache_writes() {
  local do_cache=0

  if [ "$RUN_CACHE_CASES" = "1" ] && [ "$ROOT" = "1" ]; then
    do_cache=1
  elif [ "$RUN_CACHE_CASES" = "auto" ] && [ "$ROOT" = "1" ]; then
    do_cache=1
  fi

  if [ "$do_cache" != "1" ]; then
    log "SKIP: cache write cases need root to change dirty writeback settings"
    return 0
  fi

  log "=== WRITE CACHE TESTS :: $PROFILE_LABEL ==="
  set_cache_dirty || return 0

  for rep in $(seq 1 "$REPEAT"); do
    for combo in "N N N 0 0" "Y N N 0 1"; do
      set -- $combo
      local o="$1" s="$2" w="$3" fsync_n="$4" overwrite="$5"

      for bs in "${BS_LIST[@]}"; do
        run_write_case cache "$o" "$s" "$w" "$bs" "$rep" "$fsync_n" "$overwrite"
      done
    done
  done

  restore_dirty
  drop_caches
}

run_f2fs_writes() {
  log "=== WRITE F2FS TESTS :: $PROFILE_LABEL ==="
  restore_dirty

  for rep in $(seq 1 "$REPEAT"); do
    for combo in "N N Y 0 0" "N Y N 1 0" "Y N Y 0 1" "Y Y N 1 1"; do
      set -- $combo
      local o="$1" s="$2" w="$3" fsync_n="$4" overwrite="$5"

      for bs in "${BS_LIST[@]}"; do
        run_write_case f2fs "$o" "$s" "$w" "$bs" "$rep" "$fsync_n" "$overwrite"
      done
    done
  done
}

make_median_from_summary() {
  local summary="$1"
  local median="$2"

  [ -n "$PYTHON_BIN" ] || return 0

  "$PYTHON_BIN" - "$summary" "$median" <<'PY'
import csv
import statistics
import sys

summary, median = sys.argv[1:]
key_fields = [
    "phase",
    "type",
    "order",
    "batch_read",
    "skip_ffs",
    "O",
    "S",
    "W",
    "bs",
    "rw",
    "size",
]

groups = {}
with open(summary, newline="") as f:
    reader = csv.DictReader(f, delimiter="\t")
    for row in reader:
        if not row.get("iops"):
            continue
        key = tuple(row[k] for k in key_fields)
        groups.setdefault(key, []).append(row)

out_fields = key_fields + ["n", "iops_median", "bw_MiB_s_median", "lat_mean_us_median"]

with open(median, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=out_fields, delimiter="\t")
    writer.writeheader()

    for key, rows in sorted(groups.items()):
        iops = [float(r["iops"]) for r in rows]
        bw = [float(r["bw_MiB_s"]) for r in rows]
        lat = [float(r["lat_mean_us"]) for r in rows if r.get("lat_mean_us")]

        out = dict(zip(key_fields, key))
        out["n"] = len(rows)
        out["iops_median"] = "%.3f" % statistics.median(iops)
        out["bw_MiB_s_median"] = "%.3f" % statistics.median(bw)
        out["lat_mean_us_median"] = "%.3f" % statistics.median(lat) if lat else ""
        writer.writerow(out)
PY
}

make_wide_metric_table() {
  local metric_field="$1"
  local out_path="$2"

  [ -n "$PYTHON_BIN" ] || return 0

  "$PYTHON_BIN" - "$MASTER_MEDIAN" "$PROFILE_LABELS_FILE" "$metric_field" "$out_path" <<'PY'
import csv
import sys

median_path, labels_path, metric_field, out_path = sys.argv[1:]
row_fields = ["phase", "type", "O", "S", "W", "bs", "rw", "size"]

with open(labels_path) as f:
    labels = [line.strip() for line in f if line.strip()]

rows = {}
with open(median_path, newline="") as f:
    reader = csv.DictReader(f, delimiter="\t")
    for row in reader:
        key = tuple(row[field] for field in row_fields)
        config = f"order={row['order']},batch={row['batch_read']},skip={row['skip_ffs']}"
        rows.setdefault(key, {})[config] = row.get(metric_field, "")

with open(out_path, "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t")
    writer.writerow(row_fields + labels)
    for key in sorted(rows):
        writer.writerow(list(key) + [rows[key].get(label, "") for label in labels])
PY
}

init_master_outputs() {
  mkdir -p "$TESTDIR" || exit 1
  OUTROOT="$TESTDIR/results-$(date +%Y%m%d-%H%M%S)-matrix"
  PROFILES_DIR="$OUTROOT/profiles"
  MASTER_SUMMARY="$OUTROOT/summary.tsv"
  MASTER_MEDIAN="$OUTROOT/median.tsv"
  MASTER_LOG="$OUTROOT/run.log"
  DIRTY_SAVE="$OUTROOT/dirty_sysctl.saved"
  PROFILE_MANIFEST="$OUTROOT/profiles.tsv"
  PROFILE_LABELS_FILE="$OUTROOT/config_labels.txt"
  PYTHON_BIN="$(command -v python || command -v python3 || true)"

  mkdir -p "$PROFILES_DIR" || exit 1
  write_summary_header "$MASTER_SUMMARY"
  : > "$MASTER_LOG"
  printf 'label\tdisplay_order\tdisplay_batch\tdisplay_skip\tapply_order\tapply_batch\tapply_skip\toutroot\n' > "$PROFILE_MANIFEST"
  : > "$PROFILE_LABELS_FILE"
}

init_profile_outputs() {
  CURRENT_OUTROOT="$PROFILES_DIR/$PROFILE_LABEL"
  CURRENT_JSONDIR="$CURRENT_OUTROOT/json"
  CURRENT_SUMMARY="$CURRENT_OUTROOT/summary.tsv"
  CURRENT_MEDIAN="$CURRENT_OUTROOT/median.tsv"
  CURRENT_LOG="$CURRENT_OUTROOT/run.log"

  mkdir -p "$CURRENT_JSONDIR" || exit 1
  write_summary_header "$CURRENT_SUMMARY"
  : > "$CURRENT_LOG"
}

run_profile() {
  DISPLAY_ORDER="$1"
  DISPLAY_BATCH="$2"
  DISPLAY_SKIP="$3"
  APPLY_ORDER="$4"
  APPLY_BATCH="$5"
  APPLY_SKIP="$6"
  PROFILE_LABEL="$(profile_label "$DISPLAY_ORDER" "$DISPLAY_BATCH" "$DISPLAY_SKIP")"

  init_profile_outputs
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$PROFILE_LABEL" \
    "$DISPLAY_ORDER" \
    "$DISPLAY_BATCH" \
    "$DISPLAY_SKIP" \
    "$APPLY_ORDER" \
    "$APPLY_BATCH" \
    "$APPLY_SKIP" \
    "$CURRENT_OUTROOT" >> "$PROFILE_MANIFEST"

  log "START profile: $PROFILE_LABEL"
  apply_profile_sysfs
  save_profile_metadata

  if [ "$RUN_READ" = "1" ]; then
    run_reads
  fi

  if [ "$RUN_WRITE" = "1" ]; then
    run_cache_writes
    run_f2fs_writes
  fi

  make_median_from_summary "$CURRENT_SUMMARY" "$CURRENT_MEDIAN"
  log "DONE profile: $PROFILE_LABEL"
  log "profile summary: $CURRENT_SUMMARY"
  log "profile median: $CURRENT_MEDIAN"
}

build_profile_specs
PROFILE_COUNT="$(count_profiles)"

if [ "$PROFILE_COUNT" -gt 1 ] && [ "$ROOT" != "1" ]; then
  printf 'FATAL: profile sweep requires root so sysfs knobs can be changed between runs\n' >&2
  exit 1
fi

init_master_outputs
termux-wake-lock >/dev/null 2>&1 || true
save_original_dirty
capture_original_sysfs

while IFS='|' read -r display_order display_batch display_skip apply_order apply_batch apply_skip; do
  [ -n "${display_order:-}" ] || continue
  printf '%s\n' "$(profile_label "$display_order" "$display_batch" "$display_skip")" >> "$PROFILE_LABELS_FILE"
done <<EOF
$PROFILE_SPECS
EOF

while IFS='|' read -r display_order display_batch display_skip apply_order apply_batch apply_skip; do
  [ -n "${display_order:-}" ] || continue
  run_profile "$display_order" "$display_batch" "$display_skip" "$apply_order" "$apply_batch" "$apply_skip"
done <<EOF
$PROFILE_SPECS
EOF

make_median_from_summary "$MASTER_SUMMARY" "$MASTER_MEDIAN"
make_wide_metric_table "iops_median" "$OUTROOT/median_iops.tsv"
make_wide_metric_table "bw_MiB_s_median" "$OUTROOT/median_bw_MiB_s.tsv"
make_wide_metric_table "lat_mean_us_median" "$OUTROOT/median_lat_mean_us.tsv"

log "DONE matrix"
log "matrix summary: $MASTER_SUMMARY"
log "matrix median: $MASTER_MEDIAN"
log "matrix iops: $OUTROOT/median_iops.tsv"
log "matrix bw: $OUTROOT/median_bw_MiB_s.tsv"
log "matrix lat: $OUTROOT/median_lat_mean_us.tsv"
