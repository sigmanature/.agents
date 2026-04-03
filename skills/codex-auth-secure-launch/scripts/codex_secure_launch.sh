#!/usr/bin/env bash
set -euo pipefail

DEFAULT_AUTH_JSON="${HOME}/.codex/auth.json"
DEFAULT_JSON_KEY="OPENAI_API_KEY"
DEFAULT_ENV_KEY="OPENAI_API_KEY"
DEFAULT_PASS_ENV="CODEX_AUTH_PASSPHRASE"
OPENSSL_CIPHER="aes-256-cbc"
OPENSSL_ITER=200000

usage() {
  cat <<'EOF'
Usage:
  codex_secure_launch.sh init --output <encrypted-file> [options]
  codex_secure_launch.sh run --encrypted-file <encrypted-file> [options] [-- <codex-args...>]

Commands:
  init
      Read one field from an auth.json file and store only that value in encrypted form.

  run
      Decrypt the encrypted value into an environment variable and launch codex.

Common options:
  --pass-env <name>      Passphrase environment variable name.
                         default: CODEX_AUTH_PASSPHRASE

init options:
  --output <path>        Destination encrypted file. Required.
  --source <path>        auth.json source file.
                         default: ~/.codex/auth.json
  --json-key <name>      JSON field to extract.
                         default: OPENAI_API_KEY

run options:
  --encrypted-file <p>   Encrypted file created by init. Required.
  --env-key <name>       Environment variable to export for codex.
                         default: OPENAI_API_KEY
  --provider <name>      Add -c model_providers.<name>.env_key="<env-key>" when launching codex.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

read_passphrase() {
  local pass_env="$1"
  local confirm="${2:-0}"
  local first second

  if [[ -n "${!pass_env:-}" ]]; then
    printf '%s' "${!pass_env}"
    return 0
  fi

  if [[ ! -t 0 ]]; then
    die "passphrase env ${pass_env} is unset and no TTY is available"
  fi

  read -r -s -p "Passphrase: " first
  echo >&2

  if [[ "$confirm" == "1" ]]; then
    read -r -s -p "Confirm passphrase: " second
    echo >&2
    [[ "$first" == "$second" ]] || die "passphrases do not match"
  fi

  printf '%s' "$first"
}

init_cmd() {
  local output="" source="$DEFAULT_AUTH_JSON" json_key="$DEFAULT_JSON_KEY" pass_env="$DEFAULT_PASS_ENV"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output)
        output="${2:-}"
        shift 2
        ;;
      --source)
        source="${2:-}"
        shift 2
        ;;
      --json-key)
        json_key="${2:-}"
        shift 2
        ;;
      --pass-env)
        pass_env="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown init option: $1"
        ;;
    esac
  done

  [[ -n "$output" ]] || die "--output is required"
  [[ -f "$source" ]] || die "source auth json not found: $source"

  require_cmd jq
  require_cmd openssl

  local key_value passphrase tmp_output
  key_value="$(jq -r --arg k "$json_key" '.[$k] // empty' "$source")"
  [[ -n "$key_value" ]] || die "field $json_key not found or empty in $source"

  passphrase="$(read_passphrase "$pass_env" 1)"

  umask 077
  mkdir -p "$(dirname "$output")"
  tmp_output="$(mktemp "${output}.tmp.XXXXXX")"
  trap 'rm -f "$tmp_output"' EXIT

  printf '%s' "$key_value" | \
    openssl enc -"${OPENSSL_CIPHER}" -pbkdf2 -iter "$OPENSSL_ITER" -salt -a \
      -pass "pass:${passphrase}" -out "$tmp_output"

  mv "$tmp_output" "$output"
  trap - EXIT
  chmod 600 "$output"
  echo "Encrypted key written to $output"
}

run_cmd() {
  local encrypted_file="" env_key="$DEFAULT_ENV_KEY" provider="" pass_env="$DEFAULT_PASS_ENV"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --encrypted-file)
        encrypted_file="${2:-}"
        shift 2
        ;;
      --env-key)
        env_key="${2:-}"
        shift 2
        ;;
      --provider)
        provider="${2:-}"
        shift 2
        ;;
      --pass-env)
        pass_env="${2:-}"
        shift 2
        ;;
      --)
        shift
        break
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        break
        ;;
    esac
  done

  [[ -n "$encrypted_file" ]] || die "--encrypted-file is required"
  [[ -f "$encrypted_file" ]] || die "encrypted file not found: $encrypted_file"

  require_cmd openssl
  require_cmd codex

  local passphrase api_key
  passphrase="$(read_passphrase "$pass_env" 0)"
  api_key="$(openssl enc -d -"${OPENSSL_CIPHER}" -pbkdf2 -iter "$OPENSSL_ITER" -a \
    -pass "pass:${passphrase}" -in "$encrypted_file")"
  [[ -n "$api_key" ]] || die "decrypted value is empty"

  local -a cmd=(codex)
  if [[ -n "$provider" ]]; then
    cmd+=(-c "model_providers.${provider}.env_key=\"${env_key}\"")
  fi

  if [[ $# -gt 0 ]]; then
    cmd+=("$@")
  fi

  exec env "${env_key}=${api_key}" "${cmd[@]}"
}

main() {
  local subcmd="${1:-}"
  if [[ -z "$subcmd" || "$subcmd" == "-h" || "$subcmd" == "--help" ]]; then
    usage
    exit 0
  fi
  shift || true

  case "$subcmd" in
    init)
      init_cmd "$@"
      ;;
    run)
      run_cmd "$@"
      ;;
    *)
      die "unknown command: $subcmd"
      ;;
  esac
}

main "$@"
