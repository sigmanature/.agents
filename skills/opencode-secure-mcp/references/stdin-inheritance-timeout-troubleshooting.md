# opencode Secure MCP stdin-Inheritance Timeout Troubleshooting

## High-signal symptom

- direct `scripts/opencode_secure_run.sh --model ... -- 'prompt'` succeeds
- the same model and prompt through `opencode_run_task` times out
- timeout diagnostics show little or no stdout/stderr
- `trace` mode may show only the initial `opencode run` startup log line

## Root cause to check first

Do not let the child `opencode run` process inherit the MCP server's stdin.

When the MCP server is speaking stdio JSON-RPC, its stdin is a live transport pipe. If `opencode` inherits that pipe, it can treat the stream as its own input source and wait for EOF forever even though the prompt was already passed as a CLI argument.

## Fast verification

Reproduce the same prompt in two ways:

- run the wrapper directly with stdin closed or `/dev/null`
- run the same command with an open pipe as stdin

If the direct `/dev/null` case completes but the open-pipe case hangs, the provider/model is not the primary problem. The subprocess stdin wiring is.

## Fix

In the MCP server, launch synchronous and background `opencode` processes with `stdin=subprocess.DEVNULL`.

Apply the same rule to both:

- `subprocess.run(...)`
- `subprocess.Popen(...)`

## Boundary reminder

This is an orchestration bug in the MCP server, not a reason to move decryption or passphrase handling out of `scripts/opencode_secure_run.sh`.
