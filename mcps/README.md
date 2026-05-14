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

Install the same MCP into OpenCode so `opencode` can discover it directly:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor opencode
```

Preview without writing:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope user --vendor codex --dry-run
```

Project-scope Roo install uses `.roo/mcp.json`:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope project --vendor roo --workspace /path/to/workspace
```

Project-scope OpenCode install writes `<workspace>/opencode.json` unless an existing `opencode.jsonc` is present:

```bash
python3 ~/.agents/install_mcps.py opencode_secure --scope project --vendor opencode --workspace /path/to/workspace
```

Codex MCP registration is global in the current Codex CLI, so use `--scope user` for Codex targets. Claude supports `user` and `project` scopes through its CLI. Roo project scope writes `<workspace>/.roo/mcp.json`. OpenCode writes the MCP map into `~/.config/opencode/opencode.json` for user scope and into the workspace `opencode.json` or `opencode.jsonc` for project scope.

Manifest fields:

- `name`: MCP server name.
- `transport`: `stdio`, `http`, or `sse`.
- `command` and `args`: stdio launch command.
- `url`: HTTP/SSE endpoint.
- `env`: optional environment variables.
- `cwd`: optional working directory, currently written for Roo JSON.
- `vendors`: target vendors, currently `codex`, `claude`, `roo`, and `opencode`.
- `roo`: Roo-specific options such as `disabled` and `alwaysAllow`.
