---
name: kiro-cli-agent-config-debug
description: Debug and repair Kiro CLI agent configurations when tools are printed as literal XML/text, tool calls never execute, approvals still appear after configuring trust, or reads/writes outside the workspace fail. Use when working with ~/.kiro/agents/*.json, ~/.kiro/settings/cli.json, Kiro CLI sessions under ~/.kiro/sessions/cli, or commands such as kiro-cli agent validate/list/set-default and kiro-cli chat --no-interactive.
---

# Kiro Cli Agent Config Debug

## Overview

Use this skill to distinguish three separate Kiro CLI agent concerns:

- tool mounting: `tools` controls which tools are exposed to the model
- trust: `allowedTools` and `--trust-all-tools` control approval behavior
- per-tool policy: `toolsSettings` controls paths and command allow rules

The common failure pattern is an agent JSON that configures trust or paths but omits `tools`; Kiro accepts the config, but the model can only emit tool-use markup as text because no tool schema was mounted.

## Workflow Contract

### Main Workflow

1. Inspect active defaults: `kiro-cli settings list --format json-pretty` and `kiro-cli agent list`.
2. Read the active agent JSON in `~/.kiro/agents/<name>.json` and any project override under `.kiro/agents`.
3. Validate the agent config with `kiro-cli agent validate --path <agent-json>`.
4. Inspect recent session records in `~/.kiro/sessions/cli/*.json` or `*.jsonl` for `builtin_tool_uses`, literal `<tool_use>` text, `agent_name`, `trusted_tools`, and filesystem path permissions.
5. Repair the agent JSON using explicit tool mounting plus trust and canonical tool settings.
6. Run no-interactive smoke tests for shell, out-of-workspace read, and safe temp write.
7. Report the root cause, exact file changed, and smoke-test evidence.

### Decision Table

| Phase | Trigger / Symptom | Action | Verify | On Failure | Workflow Effect |
|---|---|---|---|---|---|
| Preflight | User says Kiro prints `<tool_use>` or tool calls are not executed | Check active agent and session `builtin_tool_uses` | Failed sessions show text markup and zero real tool uses | Compare with a known working built-in/default session | Branch to tool mounting before trust/path debugging |
| Agent config | Agent JSON has `allowedTools` or `toolsSettings` but no `tools` | Add `"tools": ["*"]` or a precise tool list | `kiro-cli agent validate --path ...` exits 0 | Generate a temp default agent with `kiro-cli agent create --from kiro_default --directory /tmp/...` and compare schema | Block further approval/path tuning until tools are mounted |
| Trust | Agent uses unsupported aggregate such as `"@builtin"` in `allowedTools` | Prefer `"allowedTools": ["*"]` for a full-access diagnostic agent, or use specific tool names for narrower agents | No-interactive shell/read/write tests do not require approval | Start chat with `--trust-all-tools` to isolate trust from mounting | Replace invalid trust pattern |
| Tool settings | Path or shell policy keys are written as aliases like `read`, `write`, `shell` | Use canonical keys: `fs_read`, `fs_write`, `execute_bash` | Out-of-workspace read succeeds; safe temp write succeeds | Search local Kiro release notes/code for current canonical names | Replace alias settings with canonical settings |
| Verification | Need to prove this is fixed, not just valid JSON | Run `kiro-cli chat --agent <agent> --no-interactive` smoke prompts for shell, read, write | Output says `using tool: shell/read/write` and returns expected content | Inspect new session JSONL; retry with `--trust-all-tools` to isolate approval | Continue only after real tool calls execute |

### Output Contract

- phase reached:
- decision path taken:
- verification evidence:
- fallback used:
- unresolved blocker:
- next workflow step:

## Known Good Full-Access Shape

Use this only when the user explicitly wants broad local access. Narrow the paths and tools for normal production use.

```json
{
  "name": "full-access",
  "description": "Full tool access with no approval prompts, can read/write anywhere under home",
  "tools": ["*"],
  "allowedTools": ["*"],
  "toolsSettings": {
    "fs_read": {
      "allowedPaths": ["/home/nzzhao/**", "/tmp/**", "/sys/**", "/proc/**"]
    },
    "fs_write": {
      "allowedPaths": ["/home/nzzhao/**", "/tmp/**"]
    },
    "execute_bash": {
      "autoAllowReadonly": true
    }
  }
}
```

## Smoke Tests

Run from a harmless workspace:

```bash
kiro-cli agent validate --path ~/.kiro/agents/full-access.json
kiro-cli chat --agent full-access --no-interactive 'Use the shell tool to run pwd in the current directory, then answer with only the command output.'
kiro-cli chat --agent full-access --no-interactive 'Use the file read tool, not shell, to read the first 5 lines of /home/nzzhao/.agents/skills/android-thp-fallback-sampler/SKILL.md. Then answer with only those lines.'
kiro-cli chat --agent full-access --no-interactive 'Use the file write tool to create /tmp/kiro-full-access-smoke.txt with exactly this content: kiro full-access smoke ok. Then answer with only done.'
```

Success evidence should include phrases like `using tool: shell`, `using tool: read`, or `using tool: write`. Remove any temp smoke files after verification.

## Caveats

- `kiro-cli agent validate` can pass for a semantically wrong agent, such as one missing `tools`.
- `allowedTools` is not a replacement for `tools`.
- Full-access agents are convenient for local debugging but should not be copied to shared or untrusted machines without narrowing paths and tools.
