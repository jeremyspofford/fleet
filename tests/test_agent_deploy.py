"""Check the agent deployment tree, its pointers, owned entries, and removal rules.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_deploy.py --help.
"""

import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import agent_deploy
import agent_detection
import agent_table
from common import read_toml
from test_manager import ROOT, command, repository

CATALOG = read_toml(ROOT / "agents/manifest.toml")["agents"]
MANIFEST = """
overlays = ["core", "work"]
[agents]
schema = 1
[agents.rules.style]
description = "Shared writing rules."
file = "agents/rules/style.md"
[agents.mcp.docs]
description = "Search the documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
pass_env = ["DOCS_API_KEY"]
[agents.mcp.issues]
description = "Read work issues."
transport = "http"
url = "https://mcp.example.com/issues"
bearer_token_env = "ISSUES_TOKEN"
domains = ["work"]
[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
agents = ["claude", "codex"]
[agents.hooks.guard]
description = "Stop destructive shell commands."
event = "before-tool"
tools = ["shell"]
command = "agents/hooks/guard.sh"
timeout = 10
"""
FILES = {
    ".agents/skills/notes/SKILL.md": "---\nname: notes\ndescription: Notes.\n---\n",
    "agents/rules/style.md": "Use plain words.\n",
    "agents/hooks/guard.sh": "#!/bin/sh\nexit 0\n",
}
ALL = ["claude", "codex", "gemini", "opencode", "pi"]


class Fixture(unittest.TestCase):
    """A throwaway home and content checkout with every rendered agent on."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        self.home = base / "home"
        self.home.mkdir()
        self.checkout = repository(base / "content", {**FILES, "fleet.toml": MANIFEST})
        command("git", "-C", str(self.checkout), "add", "--chmod=+x", "agents/hooks/guard.sh")
        self.profile = {"domain": "work", "agents": {name: True for name in ALL}}
        detected = patch.object(
            agent_detection, "detected", lambda home, catalog, tools=None: dict(catalog)
        )
        detected.start()
        self.addCleanup(detected.stop)

    def tearDown(self):
        self.folder.cleanup()

    def manifest(self, text=MANIFEST):
        (self.checkout / "fleet.toml").write_text(text)
        return tomllib.loads(text)

    def plan(self, text=MANIFEST):
        table = agent_table.load(self.checkout, self.manifest(text), CATALOG)
        return agent_deploy.plan(self.home, self.checkout, self.profile, table, CATALOG)

    def run_plan(self, text=MANIFEST):
        result = self.plan(text)
        agent_deploy.require_no_conflicts(result)
        agent_deploy.apply(self.home, result)
        return result

    def read_json(self, relative):
        return json.loads((self.home / relative).read_text())


class DeployTests(Fixture):
    def test_claude_and_gemini_receive_one_pointer_each_to_the_tree(self):
        self.run_plan()
        tree = self.home / agent_deploy.TREE
        self.assertEqual(os.readlink(self.home / ".claude/skills/fleet"), str(tree / "claude"))
        self.assertEqual(os.readlink(self.home / ".gemini/extensions/fleet"), str(tree / "gemini"))
        servers = self.read_json(f"{agent_deploy.TREE}/claude/.mcp.json")["mcpServers"]
        self.assertEqual(set(servers), {"fleet", "calendar"})
        self.assertEqual(servers["fleet"]["command"], str(tree / "gateway/run-claude"))
        self.assertEqual(
            servers["fleet"]["env"],
            {"DOCS_API_KEY": "${DOCS_API_KEY}", "ISSUES_TOKEN": "${ISSUES_TOKEN}"},
        )
        self.assertEqual(
            servers["calendar"], {"type": "http", "url": "https://mcp.example.com/calendar"}
        )
        served = self.read_json(f"{agent_deploy.TREE}/gateway/claude.json")["servers"]
        self.assertEqual(served["docs"]["pass_env"], ["DOCS_API_KEY"])
        self.assertEqual(served["issues"]["bearer_token_env"], "ISSUES_TOKEN")
        self.assertNotIn("description", served["docs"])
        self.assertEqual(os.readlink(tree / "claude/skills"), str(self.home / ".agents/skills"))
        rules = self.home / ".claude/rules/fleet.md"
        self.assertIn("Use plain words.", rules.read_text())
        extension = self.read_json(f"{agent_deploy.TREE}/gemini/gemini-extension.json")
        self.assertEqual(extension["contextFileName"], "GEMINI.md")
        self.assertEqual(set(extension["mcpServers"]), {"fleet"})

    def test_hooks_run_tree_wrappers_with_agent_variables_and_units(self):
        self.run_plan()
        tree = self.home / agent_deploy.TREE
        wrapper = (tree / "hooks/guard/run-claude").read_text()
        self.assertIn("export FLEET_AGENT=claude", wrapper)
        self.assertIn("export FLEET_HOOK_EVENT=before-tool", wrapper)
        self.assertEqual((tree / "hooks/guard/guard.sh").stat().st_mode & 0o777, 0o700)
        claude = self.read_json(f"{agent_deploy.TREE}/claude/hooks/hooks.json")["hooks"]
        self.assertEqual(claude["PreToolUse"][0]["matcher"], "Bash")
        self.assertEqual(claude["PreToolUse"][0]["hooks"][0]["timeout"], 10)
        gemini = self.read_json(f"{agent_deploy.TREE}/gemini/hooks/hooks.json")["hooks"]
        self.assertEqual(gemini["BeforeTool"][0]["matcher"], "run_shell_command")
        self.assertEqual(gemini["BeforeTool"][0]["hooks"][0]["timeout"], 10000)
        codex = self.read_json(".codex/hooks.json")["hooks"]["PreToolUse"][0]
        self.assertEqual(codex["hooks"][0]["command"], str(tree / "hooks/guard/run-codex"))

    def test_entry_level_agents_receive_owned_entries(self):
        result = self.run_plan()
        tree = self.home / agent_deploy.TREE
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text())["mcp_servers"]
        self.assertEqual(codex["fleet"]["env_vars"], ["DOCS_API_KEY", "ISSUES_TOKEN"])
        self.assertEqual(codex["fleet"]["command"], str(tree / "gateway/run-codex"))
        self.assertEqual(codex["calendar"], {"url": "https://mcp.example.com/calendar"})
        opencode = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(opencode["$schema"], agent_deploy.OPENCODE_SCHEMA)
        self.assertEqual(opencode["mcp"]["fleet"]["command"], [str(tree / "gateway/run-opencode")])
        self.assertEqual(
            opencode["mcp"]["fleet"]["environment"],
            {"DOCS_API_KEY": "{env:DOCS_API_KEY}", "ISSUES_TOKEN": "{env:ISSUES_TOKEN}"},
        )
        self.assertEqual(opencode["instructions"], [str(tree / "rules/opencode.md")])
        pi = self.read_json(".pi/agent/mcp.json")["mcpServers"]
        self.assertEqual(pi["fleet"]["env"]["ISSUES_TOKEN"], "${ISSUES_TOKEN}")
        wrapper = (tree / "gateway/run-pi").read_text()
        self.assertIn("scripts/gateway.py", wrapper)
        self.assertIn(str(tree / "gateway/pi.json"), wrapper)
        self.assertIn("opencode: hook guard is unsupported for this agent.", result.notes)
        self.assertIn("pi: hook guard is unsupported for this agent.", result.notes)
        self.assertIn("codex: sign in to calendar: run codex mcp login calendar", result.steps)
        self.assertIn("codex: trust new or changed Fleet hooks in /hooks.", result.steps)

    def test_steps_print_in_the_run_that_first_renders_their_item(self):
        first = self.run_plan()
        self.assertIn("codex: sign in to calendar: run codex mcp login calendar", first.steps)
        self.assertIn("codex: trust new or changed Fleet hooks in /hooks.", first.steps)
        self.assertEqual(self.run_plan().steps, [])
        changed = self.run_plan(MANIFEST.replace("timeout = 10", "timeout = 20"))
        self.assertEqual(changed.steps, ["codex: trust new or changed Fleet hooks in /hooks."])

    def test_shared_files_keep_other_values_comments_and_agent_added_keys(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        original = '# My settings\nmodel = "o3"\n\n[profiles.fast]\nmodel = "mini" # quick\n'
        config.write_text(original)
        self.run_plan()
        text = config.read_text()
        # The rules key joins the top-level keys, before the first table. Every other byte stays.
        mine = "".join(
            line for line in text.splitlines(True) if not line.startswith("developer_instructions")
        )
        self.assertTrue(mine.startswith(original))
        # The agent adds a key inside Fleet's entry; that key never counts as drift.
        config.write_text(
            text.replace("[mcp_servers.fleet]\n", "[mcp_servers.fleet]\nenabled = false\n")
        )
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertNotIn(".codex/config.toml", result.files)
        changed = MANIFEST.replace(
            'pass_env = ["DOCS_API_KEY"]', 'pass_env = ["DOCS_API_KEY", "DOCS_REGION"]'
        )
        self.run_plan(changed)
        entry = tomllib.loads(config.read_text())["mcp_servers"]["fleet"]
        self.assertEqual(entry["env_vars"], ["DOCS_API_KEY", "DOCS_REGION", "ISSUES_TOKEN"])
        self.assertFalse(entry["enabled"])

    def test_unrecorded_values_stay_the_users_and_are_never_adopted(self):
        # Owner ruling 2026-11-20, point 1: the user's value wins; Fleet never takes one over.
        settings = self.home / ".pi/agent/mcp.json"
        settings.parent.mkdir(parents=True)
        rendered = next(entry.value for entry in self.plan().entries if entry.agent == "pi")
        settings.write_text(json.dumps({"mcpServers": {"fleet": {"command": "other"}}}))
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertIn(
            "pi: gateway.fleet in ~/.pi/agent/mcp.json holds your own value;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )
        settings.write_text(json.dumps({"mcpServers": {"fleet": dict(rendered, disabled=True)}}))
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.adopted, [])
        self.assertNotIn(
            "gateway.fleet", [entry.item for entry in result.entries if entry.agent == "pi"]
        )

    def test_changed_recorded_entries_are_released_and_missing_ones_return(self):
        self.run_plan()
        settings = self.home / ".pi/agent/mcp.json"
        data = json.loads(settings.read_text())
        data["mcpServers"]["fleet"]["command"] = "edited"
        settings.write_text(json.dumps(data))
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertIn("gateway.fleet", [entry.item for entry in result.released_entries])
        del data["mcpServers"]["fleet"]
        settings.write_text(json.dumps(data))
        self.run_plan()
        command = self.read_json(".pi/agent/mcp.json")["mcpServers"]["fleet"]["command"]
        self.assertTrue(command.endswith("gateway/run-pi"))

    def test_removed_declarations_remove_matching_outputs_and_keep_changed_ones(self):
        self.run_plan()
        config = self.home / ".codex/config.toml"
        config.write_text(
            config.read_text().replace("mcp.example.com/calendar", "edited.example.com")
        )
        without = (
            MANIFEST.split("[agents.connectors.calendar]")[0]
            + "[agents.hooks.guard]"
            + MANIFEST.split("[agents.hooks.guard]")[1]
        )
        without = without.replace(
            '[agents.rules.style]\ndescription = "Shared writing rules."\nfile = "agents/rules/style.md"\n',
            "",
        )
        without = without.replace(
            '[agents.mcp.issues]\ndescription = "Read work issues."\ntransport = "http"\nurl = "https://mcp.example.com/issues"\nbearer_token_env = "ISSUES_TOKEN"\ndomains = ["work"]\n',
            "",
        )
        result = self.run_plan(without)
        self.assertIn("calendar", tomllib.loads(config.read_text())["mcp_servers"])
        self.assertIn(
            "codex: connectors.calendar in ~/.codex/config.toml changed after Fleet wrote it; kept.",
            result.kept,
        )
        servers = self.read_json(f"{agent_deploy.TREE}/claude/.mcp.json")["mcpServers"]
        self.assertEqual(set(servers), {"fleet"})
        self.assertNotIn(
            "issues", self.read_json(f"{agent_deploy.TREE}/gateway/pi.json")["servers"]
        )
        self.assertFalse((self.home / ".claude/rules/fleet.md").is_symlink())
        self.assertNotIn("instructions", self.read_json(".config/opencode/opencode.json"))

    def test_domain_changes_remove_items_and_turning_an_agent_off_keeps_outputs(self):
        self.run_plan()
        served = f"{agent_deploy.TREE}/gateway/pi.json"
        self.profile["domain"] = "core"
        self.run_plan()
        self.assertNotIn("issues", self.read_json(served)["servers"])
        self.profile["agents"]["pi"] = False
        self.profile["domain"] = "work"
        result = self.run_plan()
        self.assertNotIn("issues", self.read_json(served)["servers"])
        self.assertIn("docs", self.read_json(served)["servers"])
        self.assertIn("fleet", self.read_json(".pi/agent/mcp.json")["mcpServers"])
        self.assertEqual([entry for entry in result.stale_entries if entry.agent == "pi"], [])

    def test_exceptions_turn_items_off_or_release_them_to_a_local_value(self):
        self.run_plan()
        self.profile["agent_items"] = {"mcp.docs": "off", "connectors.calendar": "local"}
        result = self.run_plan()
        self.assertNotIn(
            "docs", self.read_json(f"{agent_deploy.TREE}/gateway/codex.json")["servers"]
        )
        config = self.home / ".codex/config.toml"
        self.assertIn("calendar", tomllib.loads(config.read_text())["mcp_servers"])
        self.assertIn(
            "codex: connectors.calendar released to this machine's own value.", result.notes
        )
        inventory = json.loads((self.home / agent_deploy.INVENTORY).read_text())
        self.assertNotIn("connectors.calendar", [entry["item"] for entry in inventory["entries"]])
        config.write_text(
            config.read_text().replace("mcp.example.com/calendar", "mine.example.com")
        )
        self.assertEqual(self.plan().conflicts, [])

    def test_opencode_skips_servers_that_its_jsonc_file_overrides(self):
        jsonc = self.home / ".config/opencode/opencode.jsonc"
        jsonc.parent.mkdir(parents=True)
        jsonc.write_text(
            '{\n  // mine\n  "mcp": {"fleet": {"type": "local", "command": ["x"]},},\n}\n'
        )
        result = self.run_plan()
        self.assertNotIn("mcp", self.read_json(".config/opencode/opencode.json"))
        self.assertIn(
            "opencode: ~/.config/opencode/opencode.jsonc defines mcp.fleet, which overrides Fleet; skipped.",
            result.notes,
        )

    def test_legacy_generator_records_are_removed_or_released(self):
        target = str(self.checkout / "agents/skills/notes")
        (self.home / ".claude/skills").mkdir(parents=True)
        (self.home / ".claude/skills/notes").symlink_to(target)
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text("# Generated from agents/manifest.toml.\n")
        (self.home / ".claude/settings.json").write_text("{}\n")
        legacy = {
            ".claude/skills/notes": {"link": target},
            ".claude/settings.json": {"sha256": agent_deploy.hashlib.sha256(b"{}\n").hexdigest()},
            ".codex/config.toml": {
                "sha256": agent_deploy.hashlib.sha256(
                    b"# Generated from agents/manifest.toml.\n"
                ).hexdigest()
            },
        }
        state = self.home / agent_deploy.INVENTORY
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(legacy))
        result = self.run_plan()
        self.assertFalse((self.home / ".claude/skills/notes").is_symlink())
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertIn(".codex/config.toml", result.released)
        self.assertIn("[mcp_servers.fleet]", (self.home / ".codex/config.toml").read_text())

    def test_content_cannot_manage_an_agent_output(self):
        table = agent_table.load(self.checkout, self.manifest(), CATALOG)
        with self.assertRaises(ValueError) as caught:
            agent_deploy.plan(
                self.home, self.checkout, self.profile, table, CATALOG, {".pi/agent/mcp.json"}
            )
        self.assertIn("two sources cannot own it", str(caught.exception))

    def test_uninstall_removes_entries_but_never_deletes_shared_files(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('model = "o3"\n')
        self.run_plan()
        with tempfile.TemporaryDirectory() as workspace:
            paths, shared = agent_deploy.removal_operations(self.home, workspace)
            self.assertIn(".claude/skills/fleet", paths)
            self.assertNotIn(".codex/config.toml", paths)
            self.assertEqual(
                tomllib.loads(shared[".codex/config.toml"].read_text()), {"model": "o3"}
            )
            self.assertEqual(json.loads(shared[".pi/agent/mcp.json"].read_text()), {})

    def test_restore_returns_only_fleet_entries_from_a_backup(self):
        config = self.home / ".pi/agent/mcp.json"
        self.run_plan()
        saved = Path(self.folder.name) / "saved.json"
        saved.write_text(config.read_text())
        data = json.loads(config.read_text())
        data["mcpServers"]["fleet"]["command"] = "edited"
        data["mcpServers"]["mine"] = {"command": "mine"}
        config.write_text(json.dumps(data))
        inventory = agent_deploy.load_inventory(self.home)
        with tempfile.TemporaryDirectory() as workspace:
            restored = agent_deploy.restore_shared(
                self.home, ".pi/agent/mcp.json", saved, workspace, inventory
            )
            servers = json.loads(restored.read_text())["mcpServers"]
        self.assertTrue(servers["fleet"]["command"].endswith("gateway/run-pi"))
        self.assertEqual(servers["mine"], {"command": "mine"})


PLUGINS = """
[agents]
schema = 1
[agents.plugins.review-kit]
description = "Review commands."
[agents.plugins.review-kit.sources]
claude = { id = "review-kit@example", marketplace = "example/plugins" }
codex = { id = "review-kit@example" }
gemini = { id = "https://github.com/example/review-kit" }
opencode = { id = "opencode-review" }
pi = { id = "npm:@example/review" }
"""


class AntigravityTests(Fixture):
    """Antigravity renders only its gateway entry, the surface tested on agy 1.5.10."""

    def setUp(self):
        super().setUp()
        self.profile["agents"]["antigravity"] = True

    def test_the_gateway_entry_has_no_references_and_no_empty_args(self):
        result = self.run_plan()
        tree = self.home / agent_deploy.TREE
        servers = self.read_json(".gemini/config/mcp_config.json")["mcpServers"]
        self.assertEqual(servers, {"fleet": {"command": str(tree / "gateway/run-antigravity")}})
        served = self.read_json(f"{agent_deploy.TREE}/gateway/antigravity.json")["servers"]
        self.assertEqual(set(served), {"docs", "issues"})
        self.assertIn(
            "antigravity: rule set style is untested for this agent; Fleet writes nothing.",
            result.notes,
        )
        self.assertIn(
            "antigravity: hook guard is untested for this agent; Fleet writes nothing.",
            result.notes,
        )
        self.assertFalse((tree / "hooks/guard/run-antigravity").exists())
        self.assertFalse((tree / "rules/antigravity.md").exists())
        self.assertFalse((self.home / ".gemini/config/rules").exists())
        self.assertFalse((self.home / ".gemini/config/hooks.json").exists())

    def test_connectors_are_reported_not_written(self):
        manifest = MANIFEST.replace('agents = ["claude", "codex"]', 'agents = ["antigravity"]')
        result = self.run_plan(manifest)
        servers = self.read_json(".gemini/config/mcp_config.json")["mcpServers"]
        self.assertEqual(set(servers), {"fleet"})
        self.assertIn(
            "antigravity: connector calendar is untested for this agent; Fleet writes nothing.",
            result.notes,
        )

    def test_the_empty_file_from_agy_first_start_is_accepted(self):
        config = self.home / ".gemini/config/mcp_config.json"
        config.parent.mkdir(parents=True)
        config.write_text("")
        self.run_plan()
        self.assertIn("fleet", self.read_json(".gemini/config/mcp_config.json")["mcpServers"])

    def test_agy_rewrites_keep_the_entry_current(self):
        self.run_plan()
        config = self.home / ".gemini/config/mcp_config.json"
        # agy mcp add rewrites the file: it adds its own server and keeps Fleet's entry.
        data = json.loads(config.read_text())
        data["mcpServers"]["mine"] = {"command": "/bin/true", "disabled": False, "env": {"A": "1"}}
        config.write_text(json.dumps(data, indent=2) + "\n")
        again = self.run_plan()
        self.assertEqual(again.conflicts, [])
        self.assertIn("mine", self.read_json(".gemini/config/mcp_config.json")["mcpServers"])

    def test_plugins_are_reported_and_no_installer_runs(self):
        plugins = PLUGINS + 'antigravity = { id = "review-kit@antigravity-plugins-official" }\n'
        result = self.plan(plugins)
        self.assertIn(
            "antigravity: plugin review-kit is untested for this agent; Fleet installs nothing.",
            result.notes,
        )
        self.assertEqual(
            [plugin for plugin in result.plugins if plugin["agent"] == "antigravity"], []
        )

    def test_turning_antigravity_off_writes_nothing_new(self):
        self.profile["agents"]["antigravity"] = False
        self.run_plan()
        self.assertFalse((self.home / ".gemini/config/mcp_config.json").exists())


class PluginTests(Fixture):
    """Listing agents receive plugin entries; Codex and Gemini CLI run their installers."""

    def setUp(self):
        super().setUp()
        self.bin = Path(self.folder.name) / "bin"
        self.bin.mkdir()
        self.log = Path(self.folder.name) / "installers.log"
        for name in ALL:
            script = self.bin / name
            script.write_text(
                f'#!/bin/sh\necho "{name} $*" >> {self.log}\n'
                f"[ -e {self.folder.name}/fail-{name} ] && exit 3\nexit 0\n"
            )
            script.chmod(0o755)
        path = patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        path.start()
        self.addCleanup(path.stop)

    def calls(self):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        self.log.write_text("")
        return lines

    def deploy(self, text=PLUGINS):
        result = self.plan(text)
        agent_deploy.apply(self.home, result, CATALOG)
        return result

    def plugins(self):
        return json.loads((self.home / agent_deploy.INVENTORY).read_text())["plugins"]

    def test_listing_agents_receive_entries_and_others_run_installers(self):
        result = self.deploy()
        self.assertEqual(
            self.calls(),
            [
                "codex plugin add review-kit@example",
                "gemini extensions install https://github.com/example/review-kit --consent",
            ],
        )
        claude = self.read_json(".claude/settings.json")
        self.assertEqual(claude["enabledPlugins"], {"review-kit@example": True})
        self.assertEqual(
            claude["extraKnownMarketplaces"],
            {"example": {"source": {"source": "github", "repo": "example/plugins"}}},
        )
        self.assertEqual(
            self.read_json(".config/opencode/opencode.json")["plugin"], ["opencode-review"]
        )
        self.assertEqual(
            self.read_json(".pi/agent/settings.json")["packages"], ["npm:@example/review"]
        )
        self.assertIn("pi: the next Pi start installs npm:@example/review.", result.steps)
        self.assertTrue(
            any(step.startswith("claude: start Claude Code once") for step in result.steps)
        )
        self.assertEqual({record["agent"] for record in self.plugins()}, {"codex", "gemini"})
        rows = agent_deploy.plugin_software(result)
        self.assertEqual(
            {row["name"] for row in rows}, {"codex plugin review-kit", "gemini plugin review-kit"}
        )
        self.deploy()
        self.assertEqual(self.calls(), [])

    def test_listing_changes_follow_the_declaration_and_keep_other_values(self):
        settings = self.home / ".config/opencode/opencode.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"plugin": ["mine"]}))
        self.deploy()
        self.assertEqual(json.loads(settings.read_text())["plugin"], ["mine", "opencode-review"])
        self.calls()
        changed = PLUGINS.replace("npm:@example/review", "npm:@example/review-two")
        changed = changed.replace('opencode = { id = "opencode-review" }\n', "")
        changed = changed.replace('gemini = { id = "https://github.com/example/review-kit" }\n', "")
        self.deploy(changed)
        self.assertEqual(self.calls(), ["gemini extensions uninstall review-kit"])
        self.assertEqual(json.loads(settings.read_text())["plugin"], ["mine"])
        self.assertEqual(
            self.read_json(".pi/agent/settings.json")["packages"], ["npm:@example/review-two"]
        )

    def test_agent_off_keeps_plugins_and_local_exceptions_release_them(self):
        self.deploy()
        self.calls()
        self.profile["agents"]["pi"] = False
        self.profile["agent_items"] = {"plugins.review-kit": "local"}
        result = self.deploy()
        self.assertEqual(self.calls(), [])
        self.assertIn("codex: plugins.review-kit stays installed; Fleet released it.", result.notes)
        self.assertIn(
            "claude: plugins.review-kit released to this machine's own value.", result.notes
        )
        self.assertEqual(self.plugins(), [])
        self.assertEqual(
            self.read_json(".pi/agent/settings.json")["packages"], ["npm:@example/review"]
        )

    def test_failed_installs_stop_and_record_only_completed_work(self):
        (Path(self.folder.name) / "fail-gemini").touch()
        with self.assertRaises(RuntimeError) as caught:
            self.deploy()
        self.assertIn("gemini extensions install", str(caught.exception))
        self.assertEqual([record["agent"] for record in self.plugins()], ["codex"])

    def test_earlier_installer_records_for_listing_agents_are_released_not_uninstalled(self):
        state = self.home / agent_deploy.INVENTORY
        state.parent.mkdir(parents=True)
        record = {
            "agent": "claude",
            "item": "plugins.review-kit",
            "id": "review-kit@example",
            "marketplace": "",
        }
        state.write_text(
            json.dumps({"schema_version": 2, "paths": {}, "entries": [], "plugins": [record]})
        )
        result = self.deploy()
        self.assertNotIn("claude plugin uninstall", " ".join(self.calls()))
        self.assertIn(
            "claude: plugins.review-kit now follows the agent's own plugin listing.", result.notes
        )
        self.assertNotIn("claude", {item["agent"] for item in self.plugins()})

    def test_marketplace_addresses_map_to_claude_source_kinds(self):
        self.assertEqual(
            agent_deploy.marketplace_source("a/b"), {"source": "github", "repo": "a/b"}
        )
        self.assertEqual(
            agent_deploy.marketplace_source("https://example.com/m.git"),
            {"source": "git", "url": "https://example.com/m.git"},
        )
        self.assertEqual(
            agent_deploy.marketplace_source("/srv/m"), {"source": "directory", "path": "/srv/m"}
        )


if __name__ == "__main__":
    unittest.main()
