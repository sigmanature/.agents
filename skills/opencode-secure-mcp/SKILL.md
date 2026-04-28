---
name: opencode-secure-mcp
description: "Use when the user wants a persistent local MCP capability for secure `opencode` task execution, especially when extracting ad-hoc wrappers into a reusable bottom-layer service, running cheap external subagent work, or keeping encrypted-at-rest API keys while exposing `opencode` through stdio MCP."
---

# opencode Secure MCP

Use this skill when the user wants `opencode` exposed as a local MCP-backed capability without weakening the encrypted-at-rest launch boundary.

This skill owns the reusable bottom-layer pieces for secure `opencode` execution:

- the secure launch wrapper
- the stdio MCP server
- local registration and probe scripts

## Security Boundary

- Never read `~/.opencode/pass.txt` from the model layer.
- Never call `openssl` directly from the model layer for `opencode` launch.
- Let `scripts/opencode_secure_run.sh` perform decryption internally and inject only environment variables into the child `opencode` process.
- The MCP server must only orchestrate wrapper calls. It must not reimplement secret handling in Python.

## Primary Workflow

1. Confirm the user actually wants a reusable bottom-layer capability, not just a one-off wrapper launch.
2. Register the local stdio MCP server with Codex through `scripts/register_opencode_secure_mcp.sh` when needed.
3. Use the MCP tools for task execution; keep provider secrets encrypted at rest.
4. Validate with `scripts/test_opencode_secure_mcp.py` or a targeted probe before claiming the capability works.
5. If Codex TUI hangs on `Booting MCP server: opencode_secure`, check the stdio transport notes before changing wrapper security behavior.

## Tools

The local MCP server exposes a small task-oriented surface:

- `opencode_run_task`
- `opencode_submit_task`
- `opencode_get_task`
- `opencode_cancel_task`
- `opencode_collect_artifacts`

The interface is intentionally task-shaped, not a raw `opencode` CLI passthrough.

## When To Read References

- Read [references/tool-contract.md](references/tool-contract.md) when you need the MCP input/output contract.
- Read [references/security-boundary.md](references/security-boundary.md) when adjusting how encrypted keys flow into `opencode`.
- Read [references/stdio-transport-troubleshooting.md](references/stdio-transport-troubleshooting.md) when startup succeeds at the process level but Codex never finishes MCP boot.

## Scripts

- [scripts/opencode_secure_run.sh](scripts/opencode_secure_run.sh)
- [scripts/opencode_secure_mcp_server.py](scripts/opencode_secure_mcp_server.py)
- [scripts/register_opencode_secure_mcp.sh](scripts/register_opencode_secure_mcp.sh)
- [scripts/test_opencode_secure_mcp.py](scripts/test_opencode_secure_mcp.py)
