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
- secure launch parameters: `encrypted_file`, `pass_file`, `pass_env`, `env_keys`
- tracing: `request_id`, `tags`

## Stable Output Groups

- identity: `job_id`
- lifecycle: `status`, `summary`
- result payload: `stdout`, `stderr`, `artifacts`
- failure surface: `error.code`, `error.message`
- timing: `submitted_at`, `started_at`, `finished_at`
