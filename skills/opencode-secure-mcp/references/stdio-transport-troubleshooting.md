# opencode Secure MCP Stdio Transport Troubleshooting

## High-signal symptom

- Codex TUI stays on `Booting MCP server: opencode_secure`
- after about 30 seconds it reports MCP startup timeout
- wrapper log shows process start
- server log shows only `server start`, or blocks before first decoded request

## Root cause to check first

For MCP over `stdio`, use newline-delimited JSON-RPC messages, one JSON object per line.

Do not implement the transport as LSP-style `Content-Length` headers unless you are intentionally keeping a backwards-compatibility fallback for local probes.

If the server reads `Content-Length` headers while the client sends newline-delimited JSON, the first JSON line may be consumed as if it were a header and the server can block forever waiting for a blank header terminator that never arrives.

## Fast verification

Healthy startup should show this sequence in the debug log:

- `recv ndjson method=initialize`
- `recv ndjson method=notifications/initialized`
- `recv ndjson method=tools/list`

If the server process is alive and blocked in pipe read but these lines never appear, inspect transport framing before touching secret-handling code.

## Boundary reminder

Transport fixes belong in the MCP server.

Do not move decryption into Python just because startup is failing. The secure launch boundary still belongs in `scripts/opencode_secure_run.sh`.
