# opencode Secure MCP Tool Contract

## Design Intent

The MCP surface is task-oriented and stable across three layers:

1. thin human wrapper commands
2. direct MCP calls
3. future Codex-to-opencode bridge logic

The public contract should describe:

- what work to run
- where to run it
- which model to use
- how long it may run
- what result shape to return

The public contract should not expose:

- raw shell snippets
- raw `openssl` arguments
- passphrase contents
- arbitrary environment mutation

## Tool Summary

- `opencode_run_task`: synchronous task execution
- `opencode_submit_task`: background task execution
- `opencode_get_task`: status lookup
- `opencode_cancel_task`: best-effort cancellation
- `opencode_collect_artifacts`: log and artifact retrieval

## Stable Input Groups

- task intent: `instruction`, `task_type`
- location: `cwd`
- execution policy: `model`, `timeout_sec`
- diagnostics policy: `diagnostics.mode`, tail-capture limits, artifact persistence, and opencode debug flags
- secure launch parameters: `encrypted_file`, `pass_file`, `pass_env`, `env_keys`
- tracing: `request_id`, `tags`

## Stable Output Groups

- identity: `job_id`
- lifecycle: `status`, `summary`
- execution metadata: `cwd`, `command`, `artifact_paths`
- result payload: `stdout`, `stderr`, `artifacts`
- failure surface: `error.code`, `error.message`
- failure diagnostics: `diagnostics.mode`, `stdout_tail`, `stderr_tail`, `artifact_paths`
- timing: `submitted_at`, `started_at`, `finished_at`

## Diagnostics Defaults

- Default caller posture should be `diagnostics.mode=on_error`.
- `on_error` keeps successful runs quiet but enriches timeout and nonzero-exit responses with stdout/stderr tails and artifact paths.
- `trace` is for active debugging and may add `opencode` logging flags such as `--print-logs` and `--log-level DEBUG`.
- `off` is only for callers that intentionally want the smallest possible failure surface.
