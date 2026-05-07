#!/usr/bin/env bash
set -euo pipefail

DEFAULT_PASS_ENV="CODEX_AUTH_PASSPHRASE"
DEFAULT_PASS_FILE="${HOME}/.opencode/pass.txt"
DEFAULT_ENCRYPTED_FILE="${HOME}/.opencode/auth.key.enc"
OPENSSL_CIPHER="aes-256-cbc"
OPENSSL_ITER=200000
DEFAULT_MODEL="${OPENCODE_SECURE_DEFAULT_MODEL:-Mify-Moon/moonshot/kimi-k2.6}"
DEFAULT_ENV_KEYS=(
  "MIFY_API_KEY"
  "OPENAI_API_KEY"
  "KIMI_API_KEY"
  "MOONSHOT_API_KEY"
  "MINIMAX_API_KEY"
  "ZHIPU_API_KEY"
)

usage() {
  cat <<'EOF'
Usage:
  opencode_secure_run.sh [options] [-- <opencode-run-args...>]

Options:
  --encrypted-file <path>  Encrypted key file created by codex_secure_launch.sh init.
                           default: ~/.opencode/auth.key.enc
  --model <provider/model> Model passed to opencode run.
                           default: Mify-Moon/moonshot/kimi-k2.6
  --pass-env <name>        Passphrase environment variable name.
                           default: CODEX_AUTH_PASSPHRASE
  --pass-file <path>       Passphrase file read only by this wrapper when the env var is unset.
                           default: ~/.opencode/pass.txt
  --env-key <name>         Add one more environment variable to receive the decrypted key.
                           May be passed multiple times.

Notes:
  - This wrapper is non-interactive.
  - Passphrase lookup order: chosen env var first, then the chosen pass file.
  - The decrypted key is only injected into the child opencode process environment.
  - The MCP server resolves omitted or short model selectors before invoking this wrapper.
  - Set OPENCODE_SECURE_DEFAULT_MODEL to override the direct-wrapper fallback model.
  - Remaining arguments are appended after 'opencode run -m <model>'.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

main() {
  local encrypted_file="$DEFAULT_ENCRYPTED_FILE" model="$DEFAULT_MODEL" pass_env="$DEFAULT_PASS_ENV" pass_file="$DEFAULT_PASS_FILE"
  local -a extra_env_keys=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --encrypted-file)
        encrypted_file="${2:-}"
        shift 2
        ;;
      --model)
        model="${2:-}"
        shift 2
        ;;
      --pass-env)
        pass_env="${2:-}"
        shift 2
        ;;
      --pass-file)
        pass_file="${2:-}"
        shift 2
        ;;
      --env-key)
        extra_env_keys+=("${2:-}")
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

  [[ -f "$encrypted_file" ]] || die "encrypted file not found: $encrypted_file"
  [[ -n "$model" ]] || die "--model must not be empty"

  require_cmd openssl
  require_cmd opencode

  local -a pass_args=()
  if [[ -n "${!pass_env:-}" ]]; then
    pass_args=(-pass "pass:${!pass_env}")
  else
    [[ -f "$pass_file" ]] || die "passphrase env ${pass_env} is unset and pass file not found: $pass_file"
    pass_args=(-pass "file:${pass_file}")
  fi

  local api_key
  if ! api_key="$(openssl enc -d -"${OPENSSL_CIPHER}" -pbkdf2 -iter "$OPENSSL_ITER" -a \
    "${pass_args[@]}" -in "$encrypted_file" 2>/dev/null)"; then
    api_key="$(openssl enc -d -"${OPENSSL_CIPHER}" -pbkdf2 -iter "$OPENSSL_ITER" \
      "${pass_args[@]}" -in "$encrypted_file")"
  fi
  [[ -n "$api_key" ]] || die "decrypted value is empty"

  local -a env_args=()
  local env_key
  for env_key in "${DEFAULT_ENV_KEYS[@]}" "${extra_env_keys[@]}"; do
    [[ -n "$env_key" ]] || die "encountered empty env key name"
    env_args+=("${env_key}=${api_key}")
  done

  exec env "${env_args[@]}" opencode run -m "$model" "$@"
}

main "$@"
