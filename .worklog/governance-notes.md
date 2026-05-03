### Workflow Candidate
- owning skill: opencode-secure-mcp
- phase: registration / migration
- trigger / symptom: MCP registration was only captured as a Codex-specific script, while skills and AGENTS.md already had cross-vendor installers.
- action: add top-level `install_mcps.py` plus manifest(s) under `~/.agents/mcps/`.
- verify: unit tests, dry-run, and real Codex reinstall/list validation.
- fallback: use `skills/opencode-secure-mcp/scripts/register_opencode_secure_mcp.sh` for Codex-only recovery.
- workflow effect: branch
- promote to: `install_mcps.py`, `mcps/opencode_secure.json`, and `opencode-secure-mcp/SKILL.md`
- status: promoted

Promotion evidence:
- script: `/home/nzzhao/.agents/install_mcps.py`
- manifest: `/home/nzzhao/.agents/mcps/opencode_secure.json`
- reference: `/home/nzzhao/.agents/mcps/README.md`
- workflow: `/home/nzzhao/.agents/skills/opencode-secure-mcp/SKILL.md`
- real one-click validation: `python3 /home/nzzhao/.agents/install_mcps.py --scope user --all`
