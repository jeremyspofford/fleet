"""Check the owner's rulings of 2026-11-20 on agent outputs: points 1, 7, and 9.

Point 1: a user's value at Fleet's target wins, and Fleet stops managing it.
Point 7: Codex receives the rule text in developer_instructions; Pi links APPEND_SYSTEM.md.
Point 9: a plugin the user installed stays the user's.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_ruled_points.py --help.
"""

import json
import tomllib
import unittest

import agent_deploy
from test_agent_deploy import MANIFEST, Fixture, PluginTests

PI = ".pi/agent/mcp.json"


def inventory(home):
    return json.loads((home / agent_deploy.INVENTORY).read_text())


def recorded(home, agent, item):
    return [
        entry
        for entry in inventory(home).get("entries", [])
        if entry["agent"] == agent and entry["item"] == item
    ]


class UserValueTests(Fixture):
    """Point 1: Fleet keeps the user's value, prints one line once, and never stops a run."""

    def run_plan(self, text=MANIFEST):
        result = self.plan(text)
        # Unattended runs complete: no case is a conflict.
        self.assertEqual(result.conflicts, [])
        agent_deploy.require_no_conflicts(result)
        agent_deploy.apply(self.home, result)
        return result

    def test_a_changed_recorded_entry_is_released_and_reported_once(self):
        self.run_plan()
        settings = self.home / PI
        data = json.loads(settings.read_text())
        data["mcpServers"]["fleet"]["command"] = "edited"
        settings.write_text(json.dumps(data))
        before = settings.read_bytes()
        result = self.run_plan()
        line = (
            "pi: gateway.fleet in ~/.pi/agent/mcp.json holds your own value;"
            " Fleet leaves it and stopped managing it."
        )
        self.assertIn(line, result.steps)
        self.assertEqual(settings.read_bytes(), before)
        self.assertEqual(recorded(self.home, "pi", "gateway.fleet"), [])
        self.assertEqual(agent_deploy.drift(self.home, self.profile), [])
        self.assertNotIn(line, self.run_plan().steps)
        # Deleting the released value lets Fleet write and record it again.
        del data["mcpServers"]["fleet"]
        settings.write_text(json.dumps(data))
        self.run_plan()
        command = self.read_json(PI)["mcpServers"]["fleet"]["command"]
        self.assertTrue(command.endswith("gateway/run-pi"))
        self.assertTrue(recorded(self.home, "pi", "gateway.fleet"))

    def test_unrecorded_values_stay_unrecorded_whether_equal_or_different(self):
        settings = self.home / PI
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"mcpServers": {"fleet": {"command": "other"}}}))
        result = self.run_plan()
        self.assertIn(
            "pi: gateway.fleet in ~/.pi/agent/mcp.json holds your own value;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )
        self.assertEqual(self.read_json(PI), {"mcpServers": {"fleet": {"command": "other"}}})
        self.assertEqual(recorded(self.home, "pi", "gateway.fleet"), [])

    def test_an_equal_unrecorded_value_is_never_taken_over(self):
        rendered = next(entry.value for entry in self.plan().entries if entry.agent == "pi")
        settings = self.home / PI
        settings.parent.mkdir(parents=True)
        text = json.dumps({"mcpServers": {"fleet": rendered}}, indent=4)
        settings.write_text(text)
        result = self.run_plan()
        self.assertEqual(settings.read_text(), text)
        self.assertEqual(recorded(self.home, "pi", "gateway.fleet"), [])
        self.assertEqual(result.adopted, [])

    def test_a_pointer_the_user_owns_stays_and_is_not_recorded(self):
        pointer = self.home / ".claude/rules/fleet.md"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("My own rules.\n")
        result = self.run_plan()
        self.assertIn(
            "claude: ~/.claude/rules/fleet.md holds your own file;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )
        self.assertEqual(pointer.read_text(), "My own rules.\n")
        self.assertNotIn(".claude/rules/fleet.md", inventory(self.home)["paths"])
        self.assertEqual(agent_deploy.drift(self.home, self.profile), [])

    def test_a_replaced_recorded_pointer_prints_one_line(self):
        self.run_plan()
        pointer = self.home / ".claude/rules/fleet.md"
        pointer.unlink()
        pointer.write_text("Mine now.\n")
        result = self.run_plan()
        lines = [line for line in result.steps + result.kept + result.notes if "fleet.md" in line]
        self.assertEqual(
            lines,
            [
                "claude: ~/.claude/rules/fleet.md holds your own file;"
                " Fleet leaves it and stopped managing it."
            ],
        )
        self.assertEqual(pointer.read_text(), "Mine now.\n")
        self.assertNotIn(".claude/rules/fleet.md", inventory(self.home)["paths"])

    def test_an_edited_tree_file_is_still_replaced(self):
        self.run_plan()
        tree = self.home / agent_deploy.TREE / "rules/claude.md"
        tree.write_text("edited\n")
        result = self.run_plan()
        self.assertNotEqual(tree.read_text(), "edited\n")
        self.assertTrue(any("Replaced edited" in note for note in result.notes))


class CodexRulesTests(Fixture):
    """Point 7: the rule text lives in the top-level developer_instructions key."""

    def config(self):
        return self.home / ".codex/config.toml"

    def test_rules_reach_codex_and_every_other_byte_stays(self):
        config = self.config()
        config.parent.mkdir(parents=True)
        mine = '# my settings\nmodel = "o4"\n\n[profiles.fast]\nmodel = "o4-mini"\n'
        config.write_text(mine)
        result = self.run_plan()
        self.assertNotIn(
            "codex: rule sets are unsupported; Codex reads no include file.", result.notes
        )
        text = config.read_text()
        self.assertTrue(text.startswith('# my settings\nmodel = "o4"\ndeveloper_instructions = '))
        self.assertIn('\n\n[profiles.fast]\nmodel = "o4-mini"\n', text)
        self.assertIn("Use plain words.", tomllib.loads(text)["developer_instructions"])
        self.assertTrue(recorded(self.home, "codex", "rules"))
        # A rule change rewrites only that key.
        (self.checkout / "agents/rules/style.md").write_text("Write short commits.\n")
        from test_manager import command

        command("git", "-C", str(self.checkout), "commit", "-qm", "rules", "agents/rules/style.md")
        self.run_plan()
        after = config.read_text()
        self.assertIn("Write short commits.", tomllib.loads(after)["developer_instructions"])

        def others(content):
            return [line for line in content.splitlines() if "developer_instructions" not in line]

        self.assertEqual(others(after), others(text))
        # Removing every rule set removes the key, because it still matches its record.
        block = '[agents.rules.style]\ndescription = "Shared writing rules."\nfile = "agents/rules/style.md"\n'
        self.assertIn(block, MANIFEST)
        self.run_plan(MANIFEST.replace(block, ""))
        final = tomllib.loads(config.read_text())
        self.assertNotIn("developer_instructions", final)
        self.assertEqual(final["profiles"], {"fast": {"model": "o4-mini"}})

    def test_tables_only_and_multi_line_strings_keep_their_bytes(self):
        config = self.config()
        config.parent.mkdir(parents=True)
        for mine in (
            '[profiles.fast]\nmodel = "o4-mini"\n',
            'notes = """\n[not.a.table]\n"""\nmodel = "o4"\n\n[profiles.fast]\nmodel = "m"\n',
        ):
            config.write_text(mine)
            self.run_plan()
            text = config.read_text()
            others = "".join(
                line
                for line in text.splitlines(True)
                if not line.startswith("developer_instructions")
            )
            self.assertTrue(others.startswith(mine), text)
            parsed = tomllib.loads(text)
            self.assertIn("Use plain words.", parsed["developer_instructions"])
            (self.home / agent_deploy.INVENTORY).unlink()

    def test_the_users_own_instructions_stay(self):
        config = self.config()
        config.parent.mkdir(parents=True)
        config.write_text('developer_instructions = "Mine."\n')
        result = self.run_plan()
        self.assertEqual(tomllib.loads(config.read_text())["developer_instructions"], "Mine.")
        self.assertIn(
            "codex: rules in ~/.codex/config.toml holds your own value;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )


class PiRulesTests(Fixture):
    """Point 7: Pi reads a linked APPEND_SYSTEM.md, as tested with Pi 1.3.0 on 2026-11-20."""

    def test_pi_links_its_rules_and_removes_the_link_with_the_last_rule(self):
        result = self.run_plan()
        link = self.home / agent_deploy.PI_RULES
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            link.read_text(), (self.home / agent_deploy.TREE / "rules/pi.md").read_text()
        )
        self.assertIn("Use plain words.", link.read_text())
        self.assertNotIn("pi: rule sets are unsupported; Pi reads no include file.", result.notes)
        self.assertIn(agent_deploy.PI_RULES, inventory(self.home)["paths"])
        block = '[agents.rules.style]\ndescription = "Shared writing rules."\nfile = "agents/rules/style.md"\n'
        self.run_plan(MANIFEST.replace(block, ""))
        self.assertFalse(link.is_symlink() or link.exists())

    def test_an_append_system_file_the_user_wrote_stays(self):
        mine = self.home / agent_deploy.PI_RULES
        mine.parent.mkdir(parents=True)
        mine.write_text("My own Pi rules.\n")
        result = self.run_plan()
        self.assertEqual(mine.read_text(), "My own Pi rules.\n")
        self.assertFalse(mine.is_symlink())
        self.assertIn(
            "pi: ~/.pi/agent/APPEND_SYSTEM.md holds your own file;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )


class UserPluginTests(PluginTests):
    """Point 9: a plugin the user installed stays theirs. Fleet installs and records nothing."""

    def test_installed_plugins_are_left_alone_and_never_removed(self):
        codex = self.home / ".codex/config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text('[plugins."review-kit@example"]\nenabled = true\n')
        (self.home / ".gemini/extensions/review-kit").mkdir(parents=True)
        claude = self.home / ".claude/settings.json"
        claude.parent.mkdir(parents=True)
        claude.write_text(json.dumps({"enabledPlugins": {"review-kit@example": True}}))
        result = self.deploy()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.plugins(), [])
        for line in (
            "codex: plugin review-kit@example is already installed; it stays yours,"
            " and Fleet records nothing.",
            "gemini: plugin https://github.com/example/review-kit is already installed;"
            " it stays yours, and Fleet records nothing.",
        ):
            self.assertIn(line, result.steps)
        self.assertTrue(any(step.startswith("claude: plugins.review-kit") for step in result.steps))
        # A held plugin installs nothing, so no install step is printed for it.
        self.assertFalse(any(step.startswith("claude: start Claude Code") for step in result.steps))
        self.assertEqual(recorded(self.home, "claude", "plugins.review-kit"), [])
        # The next run prints no line again.
        self.assertFalse(any("already installed" in step for step in self.deploy().steps))
        # Removing the declaration uninstalls nothing and keeps the listing entry.
        self.deploy("[agents]\nschema = 1\n")
        self.assertEqual(self.calls(), [])
        self.assertEqual(
            self.read_json(".claude/settings.json")["enabledPlugins"], {"review-kit@example": True}
        )
        self.assertTrue((self.home / ".gemini/extensions/review-kit").is_dir())

    def test_a_recorded_plugin_keeps_todays_behavior(self):
        self.deploy()
        self.assertEqual(len(self.calls()), 2)
        self.deploy("[agents]\nschema = 1\n")
        self.assertEqual(
            sorted(self.calls()),
            [
                "codex plugin remove review-kit@example",
                "gemini extensions uninstall review-kit",
            ],
        )


# The imported plugin tests run in their own module only.
for name in [name for name in dir(PluginTests) if name.startswith("test_")]:
    setattr(UserPluginTests, name, None)
del PluginTests

if __name__ == "__main__":
    unittest.main()
