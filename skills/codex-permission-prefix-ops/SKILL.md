---
name: codex-permission-prefix-ops
description: Permission-aware install and test workflow for Codex. Use this whenever the user asks to install, update, or test open-source skills, MCP servers, CLIs, browser tools, or package-managed runtimes and the task may trigger sandbox approvals, network access, writes outside the workspace, or repeated "allow this command" prompts. Also use when the user mentions permissions, white lists, prefix rules, sandbox interception, "why did this read-only command get blocked", or asks to reduce repetitive approvals.
---

# Codex Permission Prefix Ops

Use this skill to keep install and validation work efficient under Codex's sandbox and approval model.

## Goals

- Minimize repeated approval prompts.
- Convert repeated benign approvals into stable `prefix_rule` requests.
- Prefer one bounded install/test script over many tiny commands.
- Verify that installed skills land in both the canonical skill store and the active vendor skill directory.

## Root-cause checklist

Before blaming the sandbox, classify the command:

1. Networked but read-only:
   - `npm view`, `npm search`, `npx skills find`
   - These still need escalation when network is disabled.
2. Writes outside writable roots:
   - `npm install -g`, browser/runtime downloads, installs under `~/.local`, `~/.agents`, `~/.codex`
3. New binary not yet allow-listed:
   - Even `--version` or read-only subcommands may prompt if the binary prefix is not approved yet.
4. GUI/browser side effects:
   - Browser launches, Chrome downloads, and similar tools often need escalation.

## Prefix-rule policy

When approval is needed for a benign recurring command family, always request a `prefix_rule`.

Choose the narrowest reusable prefix that avoids repeated prompts:

- Good:
  - `["npx", "skills"]`
  - `["npm", "view"]`
  - `["npm", "search"]`
  - `["npm", "install", "-g"]`
  - `["/home/<user>/.local/bin/agent-browser"]`
- Bad:
  - exact one-off full commands such as `["/home/<user>/.local/bin/agent-browser", "open", "https://example.com"]`
  - broad arbitrary interpreters when not already approved
  - prefixes that depend on shell redirection, env assignments, or heredocs

For direct binaries that will be reused, ask for the binary-level prefix instead of a subcommand-level prefix.

## Execution pattern

1. Inspect the target install/test flow first.
2. Group deterministic steps into one script when possible.
3. Request escalation with `justification` and reusable `prefix_rule`.
4. Install the skill/runtime.
5. Validate the landing zone and active vendor links.
6. Run one minimal smoke test instead of many fragmented checks.
7. Capture reusable permission findings in the worklog and promote them before finishing.

## Skill install rules

Two install cases matter:

### Repo-local skill you are creating or editing

Use [install_repo_skill_user.sh](scripts/install_repo_skill_user.sh) to install a copied staging copy into user vendor directories without moving the repo source tree.

```bash
bash scripts/install_repo_skill_user.sh /abs/path/to/repo/skills/my-skill
```

### External skill installed by `npx skills add`

After installation, verify:

- `~/.agents/skills/<skill-name>` exists
- `~/.codex/skills/<skill-name>` exists

If the Codex link is missing, repair it with [sync_user_skill_links.sh](scripts/sync_user_skill_links.sh):

```bash
bash scripts/sync_user_skill_links.sh <skill-name>
```

## Validation guidance

Prefer one smoke test that proves the install path works:

- CLI runtime: `--version`, then one real no-op or read-only action
- browser tool: open a simple page, read title/url, close
- MCP server: `--help` or a minimal startup/health check
- skill install: verify both canonical and vendor-visible paths

If a read-only validation command still prompts, treat it as a sign that the binary prefix was not allow-listed broadly enough.

## References

- Read [prefix-rule-playbook.md](references/prefix-rule-playbook.md) when deciding how broad the next approval request should be.
- Read [external-skill-install-notes.md](references/external-skill-install-notes.md) when `npx skills add` appears to install successfully but Codex still cannot see the skill.
