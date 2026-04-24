#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  run_device_oatdump.sh [--serial SERIAL] [--mode MODE] --package PACKAGE [--out FILE]
  run_device_oatdump.sh [--serial SERIAL] [--mode MODE] --oat-file PATH --apk-path PATH [--out FILE]

Runs device-side ART oatdump via `adb shell su -c`.

Options:
  --serial SERIAL   adb device serial
  --mode MODE       one of: header | list-classes (default: header)
  --package PKG     package name; resolves APK path with `pm path` and finds an oat/odex nearby
  --oat-file PATH   explicit device oat/odex path
  --apk-path PATH   explicit device APK path; used for package-derived lookup context and failure guidance
  --class-filter C  class name filter for `--mode list-classes`
  --require-match   exit non-zero if the filtered class is not present in the list-classes output
  --out FILE        write oatdump output to FILE instead of stdout
  -h, --help        show this help
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

trim_cr() {
  tr -d '\r'
}

serial=""
mode="header"
package=""
oat_file=""
apk_path=""
class_filter=""
require_match=0
out_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial)
      [[ $# -ge 2 ]] || die "--serial requires a value"
      serial="$2"
      shift 2
      ;;
    --mode)
      [[ $# -ge 2 ]] || die "--mode requires a value"
      mode="$2"
      shift 2
      ;;
    --package)
      [[ $# -ge 2 ]] || die "--package requires a value"
      package="$2"
      shift 2
      ;;
    --oat-file)
      [[ $# -ge 2 ]] || die "--oat-file requires a value"
      oat_file="$2"
      shift 2
      ;;
    --apk-path)
      [[ $# -ge 2 ]] || die "--apk-path requires a value"
      apk_path="$2"
      shift 2
      ;;
    --class-filter)
      [[ $# -ge 2 ]] || die "--class-filter requires a value"
      class_filter="$2"
      shift 2
      ;;
    --require-match)
      require_match=1
      shift
      ;;
    --out)
      [[ $# -ge 2 ]] || die "--out requires a value"
      out_file="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$mode" in
  header|list-classes) ;;
  *)
    die "--mode must be one of: header, list-classes"
    ;;
esac

if [[ "$mode" != "list-classes" && -n "$class_filter" ]]; then
  die "--class-filter requires --mode list-classes"
fi

if [[ "$mode" != "list-classes" && "$require_match" -eq 1 ]]; then
  die "--require-match requires --mode list-classes"
fi

if [[ "$require_match" -eq 1 && -z "$class_filter" ]]; then
  die "--require-match requires --class-filter"
fi

if [[ -n "$package" ]]; then
  [[ -z "$oat_file" && -z "$apk_path" ]] || die "use --package OR --oat-file/--apk-path, not both"
else
  [[ -n "$oat_file" && -n "$apk_path" ]] || die "explicit mode requires both --oat-file and --apk-path"
fi

adb_cmd=(adb)
if [[ -n "$serial" ]]; then
  adb_cmd+=(-s "$serial")
fi

run_root_capture() {
  local quoted_command
  printf -v quoted_command '%q ' "$@"
  "${adb_cmd[@]}" shell su -c "$quoted_command"
}

if [[ -n "$package" ]]; then
  apk_path="$("${adb_cmd[@]}" shell pm path "$package" | trim_cr | sed -n 's/^package://p' | head -n1)"
  [[ -n "$apk_path" ]] || die "could not resolve APK path for package: $package"

  package_dir="${apk_path%/*}"
  oat_candidates="$(
    run_root_capture find "$package_dir/oat" -maxdepth 2 -type f '(' -name '*.odex' -o -name '*.oat' ')' 2>/dev/null \
      | trim_cr \
      | sed '/^$/d' || true
  )"
  oat_file="$(printf '%s\n' "$oat_candidates" | sed -n '1p')"
  [[ -n "$oat_file" ]] || die "could not find an oat/odex under ${package_dir}/oat for package: $package"
fi

tmp_stdout="$(mktemp)"
tmp_stderr="$(mktemp)"
cleanup() {
  rm -f "$tmp_stdout" "$tmp_stderr"
}
trap cleanup EXIT

oatdump_args=(/apex/com.android.art/bin/oatdump)
if [[ "$mode" == "header" ]]; then
  oatdump_args+=(--header-only "--oat-file=${oat_file}")
else
  oatdump_args+=(--list-classes --no-disassemble "--oat-file=${oat_file}" "--dex-file=${apk_path}")
  if [[ -n "$class_filter" ]]; then
    oatdump_args+=("--class-filter=${class_filter}")
  fi
fi

if run_root_capture "${oatdump_args[@]}" >"$tmp_stdout" 2>"$tmp_stderr"; then
  if [[ "$mode" == "header" ]]; then
    if [[ ! -s "$tmp_stdout" ]] || ! grep -Eq 'compiler-filter = |dex2oat-cmdline = ' "$tmp_stdout"; then
      echo "oatdump produced empty or incomplete header output for: $oat_file" >&2
      cat "$tmp_stdout" >&2 || true
      exit 2
    fi
  elif [[ "$require_match" -eq 1 ]]; then
    descriptor_filter="L${class_filter//./\/};"
    if ! grep -Fq "$class_filter" "$tmp_stdout" && ! grep -Fq "$descriptor_filter" "$tmp_stdout"; then
      echo "oatdump list-classes did not report requested class: $class_filter" >&2
      exit 3
    fi
  fi
  if [[ -n "$out_file" ]]; then
    cat "$tmp_stdout" >"$out_file"
  else
    cat "$tmp_stdout"
  fi
  exit 0
fi

cat "$tmp_stderr" >&2

if [[ "$oat_file" == /data/app/* ]]; then
  staged_oat="/data/local/tmp/$(basename "$oat_file")"
  echo >&2
  echo "Direct device-side read failed for: $oat_file" >&2
  echo "Try staging the oat/odex to a more readable device path, then rerun:" >&2
  echo "  ${adb_cmd[*]} shell su -c 'cp \"$oat_file\" \"$staged_oat\" && chmod 0644 \"$staged_oat\"'" >&2
  echo "  run_device_oatdump.sh${serial:+ --serial \"$serial\"} --oat-file \"$staged_oat\" --apk-path \"$apk_path\"${out_file:+ --out \"$out_file\"}" >&2
fi

exit 1
