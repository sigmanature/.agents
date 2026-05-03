# MCP Manifests

This directory is the single source for reusable MCP registrations that should survive cross-device migration.

Install all declared MCPs into their manifest-declared vendors:

```bash
python3 ~/.agents/install_mcps.py --scope user --all
```

Install only the secure opencode MCP into Codex:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex
```

Preview without writing:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex --dry-run
```

Project-scope Roo install uses `.roo/mcp.json`:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope project --vendor roo --workspace /path/to/workspace
```

Codex MCP registration is global in the current Codex CLI, so use `--scope user` for Codex targets. Claude supports `user` and `project` scopes through its CLI. Roo project scope writes `<workspace>/.roo/mcp.json`.

Manifest fields:

- `name`: MCP server name.
- `transport`: `stdio`, `http`, or `sse`.
- `command` and `args`: stdio launch command.
- `url`: HTTP/SSE endpoint.
- `env`: optional environment variables.
- `cwd`: optional working directory, currently written for Roo JSON.
- `vendors`: target vendors, currently `codex`, `claude`, and `roo`.
- `roo`: Roo-specific options such as `disabled` and `alwaysAllow`.
