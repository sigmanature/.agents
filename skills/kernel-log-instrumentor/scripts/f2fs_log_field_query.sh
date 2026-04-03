#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  f2fs_log_field_query.sh <kernel_stream.txt> [options]

Options:
  --type <name>          Record type filter: wbdbg|sysrq_loop|sysrq_action|blocked_task|all
                         default: all
  --eq <k=v>             Exact match filter, can be repeated (AND semantics)
  --contains <k=s>       Substring filter, can be repeated (AND semantics)
  --from <sec>           Timestamp lower bound (inclusive), e.g. 84.0
  --to <sec>             Timestamp upper bound (inclusive), e.g. 85.0
  --limit <n>            Max rows to output, default: 0 (unlimited)
  --format <fmt>         tsv|csv, default: tsv
  -h, --help             Show this help

Fields:
  line ts rec_type pid comm ino seq sysrq_type sysrq_phase
  task_state task_pid task_comm msg raw
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

LOG_FILE="$1"
shift

if [[ ! -f "$LOG_FILE" ]]; then
  echo "ERROR: log file not found: $LOG_FILE" >&2
  exit 1
fi

TYPE_FILTER="all"
FROM_TS=""
TO_TS=""
LIMIT=0
FORMAT="tsv"
declare -a EQ_FILTERS=()
declare -a CONTAINS_FILTERS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)
      TYPE_FILTER="${2:-}"
      shift 2
      ;;
    --eq)
      EQ_FILTERS+=("${2:-}")
      shift 2
      ;;
    --contains)
      CONTAINS_FILTERS+=("${2:-}")
      shift 2
      ;;
    --from)
      FROM_TS="${2:-}"
      shift 2
      ;;
    --to)
      TO_TS="${2:-}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-}"
      shift 2
      ;;
    --format)
      FORMAT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! "$TYPE_FILTER" =~ ^(wbdbg|sysrq_loop|sysrq_action|blocked_task|all)$ ]]; then
  echo "ERROR: invalid --type: $TYPE_FILTER" >&2
  exit 1
fi

if [[ -n "$FROM_TS" && ! "$FROM_TS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "ERROR: invalid --from: $FROM_TS" >&2
  exit 1
fi

if [[ -n "$TO_TS" && ! "$TO_TS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "ERROR: invalid --to: $TO_TS" >&2
  exit 1
fi

if [[ ! "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo "ERROR: invalid --limit: $LIMIT" >&2
  exit 1
fi

if [[ ! "$FORMAT" =~ ^(tsv|csv)$ ]]; then
  echo "ERROR: invalid --format: $FORMAT" >&2
  exit 1
fi

join_by_us() {
  local IFS=$'\x1f'
  echo "$*"
}

EQ_JOINED="$(join_by_us "${EQ_FILTERS[@]:-}")"
CONTAINS_JOINED="$(join_by_us "${CONTAINS_FILTERS[@]:-}")"

awk -v type_filter="$TYPE_FILTER" \
    -v from_ts="$FROM_TS" \
    -v to_ts="$TO_TS" \
    -v limit="$LIMIT" \
    -v out_fmt="$FORMAT" \
    -v eq_joined="$EQ_JOINED" \
    -v contains_joined="$CONTAINS_JOINED" '
BEGIN {
  OFS = "\t"
  split(eq_joined, eq_arr, "\x1f")
  split(contains_joined, c_arr, "\x1f")
  if (out_fmt == "csv")
    OFS = ","

  print_row("line", "ts", "rec_type", "pid", "comm", "ino", "seq",
            "sysrq_type", "sysrq_phase", "task_state", "task_pid",
            "task_comm", "msg", "raw")
}

function print_row(a,b,c,d,e,f,g,h,i,j,k,l,m,n,    q) {
  if (out_fmt == "csv") {
    q = "\""
    gsub(/"/, "\"\"", a); gsub(/"/, "\"\"", b); gsub(/"/, "\"\"", c)
    gsub(/"/, "\"\"", d); gsub(/"/, "\"\"", e); gsub(/"/, "\"\"", f)
    gsub(/"/, "\"\"", g); gsub(/"/, "\"\"", h); gsub(/"/, "\"\"", i)
    gsub(/"/, "\"\"", j); gsub(/"/, "\"\"", k); gsub(/"/, "\"\"", l)
    gsub(/"/, "\"\"", m); gsub(/"/, "\"\"", n)
    print q a q, q b q, q c q, q d q, q e q, q f q, q g q, q h q, q i q, q j q, q k q, q l q, q m q, q n q
  } else {
    print a,b,c,d,e,f,g,h,i,j,k,l,m,n
  }
}

function reset_fields() {
  line = NR
  ts = ""
  rec_type = ""
  pid = ""
  comm = ""
  ino = ""
  seq = ""
  sysrq_type = ""
  sysrq_phase = ""
  task_state = ""
  task_pid = ""
  task_comm = ""
  msg = ""
  raw = $0
}

function parse_ts(    s) {
  s = $0
  if (s ~ /^\<[0-9]+\>\[[[:space:]]*[0-9]+\.[0-9]+\]/) {
    sub(/^\<[0-9]+\>\[[[:space:]]*/, "", s)
    sub(/\].*$/, "", s)
    ts = s + 0
  }
}

function get_field(name) {
  if (name == "line") return line
  if (name == "ts") return ts
  if (name == "rec_type") return rec_type
  if (name == "pid") return pid
  if (name == "comm") return comm
  if (name == "ino") return ino
  if (name == "seq") return seq
  if (name == "sysrq_type") return sysrq_type
  if (name == "sysrq_phase") return sysrq_phase
  if (name == "task_state") return task_state
  if (name == "task_pid") return task_pid
  if (name == "task_comm") return task_comm
  if (name == "msg") return msg
  if (name == "raw") return raw
  return ""
}

function pass_filters(    i, kv, k, v, p, actual) {
  if (type_filter != "all" && rec_type != type_filter)
    return 0

  if (from_ts != "" && ts != "" && (ts + 0) < (from_ts + 0))
    return 0
  if (to_ts != "" && ts != "" && (ts + 0) > (to_ts + 0))
    return 0

  for (i in eq_arr) {
    kv = eq_arr[i]
    if (kv == "")
      continue
    p = index(kv, "=")
    if (p <= 1)
      return 0
    k = substr(kv, 1, p - 1)
    v = substr(kv, p + 1)
    actual = get_field(k)
    if (actual != v)
      return 0
  }

  for (i in c_arr) {
    kv = c_arr[i]
    if (kv == "")
      continue
    p = index(kv, "=")
    if (p <= 1)
      return 0
    k = substr(kv, 1, p - 1)
    v = substr(kv, p + 1)
    actual = get_field(k)
    if (index(actual, v) == 0)
      return 0
  }
  return 1
}

{
  reset_fields()
  parse_ts()

  if (index($0, "[WBDBG]") > 0) {
    rec_type = "wbdbg"
    if (match($0, /pid=[0-9]+/))
      pid = substr($0, RSTART + 4, RLENGTH - 4)
    if (match($0, /comm=[^[:space:]]+/))
      comm = substr($0, RSTART + 5, RLENGTH - 5)
    if (match($0, /ino=[0-9]+/))
      ino = substr($0, RSTART + 4, RLENGTH - 4)
    msg = $0
    sub(/^.*\[WBDBG\]/, "", msg)
  } else if (index($0, "[SYSRQ_LOOP]") > 0) {
    rec_type = "sysrq_loop"
    if (match($0, /seq=[0-9]+/))
      seq = substr($0, RSTART + 4, RLENGTH - 4)
    if (match($0, /type=[a-z]/))
      sysrq_type = substr($0, RSTART + 5, RLENGTH - 5)
    if (match($0, /(BEGIN|END)/))
      sysrq_phase = substr($0, RSTART, RLENGTH)
    msg = $0
  } else if (index($0, "sysrq: Show Blocked State") > 0 || index($0, "sysrq: Show State") > 0) {
    rec_type = "sysrq_action"
    if (index($0, "Show Blocked State") > 0)
      sysrq_type = "w"
    else if (index($0, "Show State") > 0)
      sysrq_type = "t"
    msg = $0
  } else if ($0 ~ /task:[^[:space:]]+[[:space:]]+state:[A-Z]/ &&
             $0 ~ /pid:[0-9]+/ && $0 ~ /tgid:[0-9]+/) {
    rec_type = "blocked_task"
    if (match($0, /task:[^[:space:]]+/))
      task_comm = substr($0, RSTART + 5, RLENGTH - 5)
    if (match($0, /state:[A-Z]/))
      task_state = substr($0, RSTART + 6, RLENGTH - 6)
    if (match($0, /pid:[0-9]+/))
      task_pid = substr($0, RSTART + 4, RLENGTH - 4)
    msg = $0
  } else {
    next
  }

  if (!pass_filters())
    next

  print_row(line, ts, rec_type, pid, comm, ino, seq, sysrq_type,
            sysrq_phase, task_state, task_pid, task_comm, msg, raw)
  out_count++
  if (limit > 0 && out_count >= limit)
    exit
}
' "$LOG_FILE"
