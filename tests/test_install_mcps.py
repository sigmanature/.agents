import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_mcps", ROOT / "install_mcps.py")
install_mcps = importlib.util.module_from_spec(SPEC)
sys.modules["install_mcps"] = install_mcps
SPEC.loader.exec_module(install_mcps)


class InstallMcpsTest(unittest.TestCase):
    def manifest(self):
        return {
            "name": "demo",
            "transport": "stdio",
            "command": "bash",
            "args": ["~/.agents/demo_entry.sh"],
            "env": {"DEMO": "1"},
            "vendors": ["codex", "claude", "roo"],
        }

    def test_load_manifest_expands_home_in_command_arguments_and_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "demo.json"
            path.write_text(json.dumps(self.manifest()), encoding="utf-8")

            mcp = install_mcps.load_manifest(path, home=home)

            self.assertEqual(mcp.name, "demo")
            self.assertEqual(mcp.command, "bash")
            self.assertEqual(mcp.args, [str(home / ".agents/demo_entry.sh")])
            self.assertEqual(mcp.env, {"DEMO": "1"})

    def test_codex_and_claude_stdio_commands_are_planned(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            mcp = install_mcps.parse_manifest(self.manifest(), source=home / "demo.json", home=home)

            codex_remove, codex_add = install_mcps.build_codex_commands(mcp)
            claude_remove, claude_add = install_mcps.build_claude_commands(mcp, scope="user")

            self.assertEqual(codex_remove, ["codex", "mcp", "remove", "demo"])
            self.assertEqual(
                codex_add,
                [
                    "codex",
                    "mcp",
                    "add",
                    "--env",
                    "DEMO=1",
                    "demo",
                    "--",
                    "bash",
                    str(home / ".agents/demo_entry.sh"),
                ],
            )
            self.assertEqual(claude_remove, ["claude", "mcp", "remove", "--scope", "user", "demo"])
            self.assertEqual(
                claude_add,
                [
                    "claude",
                    "mcp",
                    "add",
                    "--scope",
                    "user",
                    "--transport",
                    "stdio",
                    "--env",
                    "DEMO=1",
                    "demo",
                    "--",
                    "bash",
                    str(home / ".agents/demo_entry.sh"),
                ],
            )

    def test_roo_install_preserves_existing_servers_and_uninstall_removes_only_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / ".roo/mcp.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps({"mcpServers": {"keep": {"command": "uvx", "args": ["x"]}}}),
                encoding="utf-8",
            )
            mcp = install_mcps.parse_manifest(self.manifest(), source=root / "demo.json", home=root)

            install_mcps.install_roo_config(config, mcp, dry_run=False)
            data = json.loads(config.read_text(encoding="utf-8"))

            self.assertIn("keep", data["mcpServers"])
            self.assertEqual(data["mcpServers"]["demo"]["command"], "bash")
            self.assertEqual(data["mcpServers"]["demo"]["args"], [str(root / ".agents/demo_entry.sh")])
            self.assertEqual(data["mcpServers"]["demo"]["env"], {"DEMO": "1"})
            self.assertFalse(data["mcpServers"]["demo"]["disabled"])

            install_mcps.uninstall_roo_config(config, "demo", dry_run=False)
            data = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(set(data["mcpServers"].keys()), {"keep"})

    def test_roo_dry_run_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / ".roo/mcp.json"
            mcp = install_mcps.parse_manifest(self.manifest(), source=root / "demo.json", home=root)

            install_mcps.install_roo_config(config, mcp, dry_run=True)

            self.assertFalse(config.exists())

    def test_codex_project_scope_is_rejected_because_codex_cli_is_global(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mcp = install_mcps.parse_manifest(self.manifest(), source=root / "demo.json", home=root)

            with self.assertRaisesRegex(ValueError, "Codex MCP registration is global"):
                install_mcps.install_cli_vendor("codex", mcp, scope="project", dry_run=True)


if __name__ == "__main__":
    unittest.main()
