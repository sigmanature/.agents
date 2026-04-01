# External Skill Install Notes

## Observed behavior

In this workspace, `npx skills add <repo@skill> -g -y` installed `agent-browser` into:

- `~/.agents/skills/agent-browser`

But it did not automatically create:

- `~/.codex/skills/agent-browser`

That means a skill can be "installed" according to the external CLI while remaining invisible to Codex until the vendor link is added.

## Required post-install check

After `npx skills add`, always verify:

```bash
ls -la ~/.agents/skills/<skill-name>
ls -la ~/.codex/skills/<skill-name>
```

If the first path exists and the second does not, sync the vendor link:

```bash
bash scripts/sync_user_skill_links.sh <skill-name>
```

## Browser runtime note

For `agent-browser`, the skill install and runtime install are separate:

1. install the skill metadata/instructions
2. install the CLI runtime
3. run `agent-browser install` to download Chrome for Testing
4. smoke test with a simple page

Do not assume the skill install provides the CLI binary or browser runtime.
