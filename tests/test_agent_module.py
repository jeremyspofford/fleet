"""Enroll with an agents table and check pointers, entries, skills, and uninstall.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_module.py --help.
"""

import json
import os
import subprocess
import tomllib
import unittest

import agent_deploy
import test_command
import test_domains
import test_manager as helpers
from test_manager import command

AGENTS = """[agents]
schema = 1
[agents.skills.personal-notes]
domains = ["personal"]
[agents.mcp.docs]
description = "Search the documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
"""
SKILL = "---\nname: {name}\ndescription: Example skill.\n---\nBody\n"


class AgentModuleTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli
    terminal = test_command.CommandTests.terminal
    commit_content = test_domains.ContentDomainCommandTests.commit_content

    def add_agents(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text('model = "o3"\n')
        manifest = (self.content / "dotfleet.toml").read_text()
        self.commit_content(
            {
                "dotfleet.toml": manifest + AGENTS,
                ".agents/skills/notes/SKILL.md": SKILL.format(name="notes"),
                ".agents/skills/personal-notes/SKILL.md": SKILL.format(name="personal-notes"),
            },
            "feat: declare agent configuration",
        )
        return command(
            "bash",
            str(self.origin / "install.sh"),
            "--defaults",
            "--agents",
            "claude,codex",
            env=self.env,
            cwd=self.home,
        )

    def test_enrollment_deploys_the_tree_pointers_entries_and_skills(self):
        self.enroll()
        output = self.add_agents()
        self.assertIn(
            "codex: sign in to calendar: run codex mcp login calendar", " ".join(output.split())
        )
        tree = self.home / agent_deploy.TREE
        self.assertEqual(os.readlink(self.home / ".claude/skills/fleet"), str(tree / "claude"))
        servers = json.loads((tree / "claude/.mcp.json").read_text())["mcpServers"]
        self.assertEqual(set(servers), {"fleet", "calendar"})
        served = json.loads((tree / "gateway/claude.json").read_text())["servers"]
        self.assertEqual(served["docs"]["command"], "npx")
        self.assertTrue((self.home / ".agents/skills/notes/SKILL.md").is_file())
        self.assertFalse((self.home / ".agents/skills/personal-notes").exists())
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertEqual(config["model"], "o3")
        self.assertEqual(config["mcp_servers"]["fleet"]["command"], str(tree / "gateway/run-codex"))
        self.assertIn("~/.codex/config.toml", output)
        self.assertEqual(self.cli("status").count("Drift             none"), 1)

        self.cli("domain", "set", "--name", "personal")
        self.assertTrue((self.home / ".agents/skills/personal-notes/SKILL.md").is_file())

        output = self.terminal(
            ["uninstall"],
            [(b"Oldest backup [keep]:", b"keep\n"), (b"Confirm [cancel]:", b"uninstall\n")],
        )
        self.assertIn("uninstall", output.lower())
        self.assertFalse((self.home / ".claude/skills/fleet").is_symlink())
        self.assertFalse(tree.exists())
        self.assertEqual(
            tomllib.loads((self.home / ".codex/config.toml").read_text()), {"model": "o3"}
        )

    def test_a_hand_written_value_stays_and_enrollment_continues(self):
        self.enroll()
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text(
            '[mcp_servers.calendar]\nurl = "https://mine.example.com"\n'
        )
        backups = sorted((self.home / ".fleet/backups").iterdir())
        manifest = (self.content / "dotfleet.toml").read_text()
        table = AGENTS.replace('[agents.skills.personal-notes]\ndomains = ["personal"]\n', "")
        self.commit_content({"dotfleet.toml": manifest + table}, "feat: declare agents")
        (self.home / ".claude").mkdir()
        process = subprocess.run(
            ["bash", str(self.origin / "install.sh"), "--defaults", "--agents", "codex"],
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
        )
        result = " ".join((process.stdout + process.stderr).split())
        # Owner ruling 2026-11-20, point 1: the user's value stays and the run continues.
        self.assertEqual(process.returncode, 0, result)
        self.assertIn(
            "connectors.calendar in ~/.codex/config.toml holds your own value;"
            " Fleet leaves it and stopped managing it.",
            result,
        )
        self.assertGreater(len(list((self.home / ".fleet/backups").iterdir())), len(backups))
        self.assertIn("mine.example.com", (self.home / ".codex/config.toml").read_text())

    def test_declared_plugins_reach_claude_through_its_own_listing(self):
        self.enroll()
        log = self.base / "claude.log"
        fake = self.base / "tools/claude"
        fake.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
        fake.chmod(0o755)
        (self.home / ".claude").mkdir()
        manifest = (self.content / "dotfleet.toml").read_text()
        plugin = (
            "[agents]\nschema = 1\n[agents.plugins.review-kit]\n"
            'description = "Review commands."\n'
            '[agents.plugins.review-kit.sources]\nclaude = { id = "review-kit@example" }\n'
        )
        self.commit_content({"dotfleet.toml": manifest + plugin}, "feat: declare a plugin")
        output = command(
            "bash",
            str(self.origin / "install.sh"),
            "--defaults",
            "--agents",
            "claude",
            env=self.env,
            cwd=self.home,
        )
        self.assertIn("~/.claude/settings.json", output)
        self.assertIn("start Claude Code once in a terminal", " ".join(output.split()))
        self.assertFalse(log.exists())  # Claude Code installs a listed plugin itself.
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertEqual(settings["enabledPlugins"], {"review-kit@example": True})

    def test_exceptions_turn_items_off_or_local_and_drift_is_reported(self):
        self.enroll()
        self.add_agents()
        self.assertIn("mcp.docs", self.cli("agents", "list"))
        refused = self.cli("agents", "set", "--name", "mcp.absent", "--state", "off", expected=1)
        self.assertIn("Unknown agent item mcp.absent", refused)
        self.assertIn(
            "choose on or off",
            self.cli("agents", "set", "--name", "skills.notes", "--state", "local", expected=1),
        )

        output = self.cli("agents", "set", "--name", "mcp.docs", "--state", "off")
        self.assert_review_golden("agents", output)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["agent_items"], {"mcp.docs": "off"})
        # With its only server off, the gateway and its entry leave this machine.
        self.assertFalse((self.home / agent_deploy.TREE / "gateway/codex.json").exists())
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertNotIn("fleet", config["mcp_servers"])

        self.cli("agents", "set", "--name", "mcp.docs", "--state", "on")
        config_path = self.home / ".codex/config.toml"
        config_path.write_text(
            config_path.read_text().replace("mcp.example.com/calendar", "my.example.com")
        )
        self.assertIn("present; run dotfleet diff", self.cli("status"))
        self.assertIn("FAIL", self.cli("doctor", expected=1))
        # Owner ruling 2026-11-20, point 1: the update keeps the user's value and continues.
        updated = " ".join(self.cli("update").split())
        self.assertIn(
            "connectors.calendar in ~/.codex/config.toml holds your own value;"
            " Fleet leaves it and stopped managing it.",
            updated,
        )
        self.assertIn("my.example.com", config_path.read_text())
        self.assertIn("Drift             none", self.cli("status"))

        self.cli("agents", "set", "--name", "connectors.calendar", "--state", "local")
        self.assertIn("my.example.com", config_path.read_text())
        self.assertIn("Drift             none", self.cli("status"))
        listing = self.cli("agents", "list")
        self.assertIn("connectors.calendar", listing)
        self.assertIn("local value on this machine", listing)

    assert_review_golden = test_command.CommandTests.assert_review_golden
    assert_golden = test_command.CommandTests.assert_golden
    record = test_command.CommandTests.record


if __name__ == "__main__":
    unittest.main()
