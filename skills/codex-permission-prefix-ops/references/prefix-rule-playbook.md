# Prefix Rule Playbook

## What caused the prompts in this task

1. `npx skills find ...`
   - Read-only in intent, but needs network.
   - Correct reusable prefix: `["npx", "skills"]`
2. `npm view ...` and `npm search ...`
   - Metadata only, but still networked.
   - Correct reusable prefixes: `["npm", "view"]`, `["npm", "search"]`
3. `npm install -g --prefix ~/.local ...`
   - Writes outside the workspace and downloads packages.
   - Correct reusable prefix: `["npm", "install", "-g"]`
4. `/home/nzzhao/.local/bin/agent-browser ...`
   - The first approval was requested too narrowly, per subcommand.
   - Better future prefix: the binary path itself, not `open`/`install`/`--version` separately.

## Decision rule

Ask:

- Is the command family likely to be repeated in this task or future install tasks?
- Is the command family benign enough that the user can safely persist it?
- Can the prefix be expressed without env assignments, pipes, or shell syntax?

If yes, request a reusable prefix.

## Granularity rules

- Package managers:
  - Prefer stable family prefixes such as `["npm", "view"]` or `["npx", "skills"]`
- Local installed tools:
  - Prefer the binary path only when the tool is broadly safe for the current job
- Project scripts:
  - Prefer `["bash", "/abs/path/to/script.sh"]` or a similarly bounded command
- Avoid:
  - full one-off commands
  - interpreter-only prefixes for arbitrary scripts unless already trusted
  - prefixes that silently broaden destructive capabilities

## Shell-shape caveats

Prefix matching is less useful when the command relies on:

- env assignments like `FOO=bar cmd`
- redirection
- pipes
- command substitution
- heredocs

When possible, move complexity into a script and approve the script runner prefix instead.

## Practical recommendation

When a task mixes discovery, install, and smoke tests:

1. request reusable package-manager prefixes early
2. install the runtime once
3. request a reusable binary-level prefix for the installed CLI
4. run a compact smoke test instead of many tiny subcommands
