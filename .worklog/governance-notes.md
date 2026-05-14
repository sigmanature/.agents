### Workflow Candidate
- owning skill: none yet (install_mcps.py governance)
- phase: discovery
- trigger / symptom: install_mcps.py lacks opencode vendor despite opencode having native MCP config
- action: treat opencode as config-backed vendor with top-level mcp map; local uses command array and environment, remote uses url
- verify: unit tests + opencode mcp list after install
- fallback: if CLI contract proves necessary later, route install through opencode SDK/CLI wrapper
- workflow effect: extend reusable MCP install flow to opencode
- promote to: mcps/README.md and install_mcps.py
- status: promoted
