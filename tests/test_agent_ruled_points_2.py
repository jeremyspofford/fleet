"""Check the owner's rulings of 2026-11-20 on agent outputs: points 5, 8, and 11.

Point 5: Claude Code gets one link per skill, so each skill keeps its name /NAME.
Point 8: a Gemini CLI extension from a local folder becomes a manual step.
Point 11: fleet uninstall uninstalls the plugins that Fleet installed.
Point 13 lives in tests/test_gateway_secrets.py and tests/test_agent_deploy.py.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_ruled_points_2.py --help.
"""

import io
import json
import os
from pathlib import Path
import contextlib
import tomllib
import unittest
from unittest.mock import patch

import agent_deploy
import agent_table
from test_agent_deploy import CATALOG, MANIFEST, PLUGINS, Fixture, PluginTests
from test_manager import repository


def inventory(home):
    return json.loads((home / agent_deploy.INVENTORY).read_text())


class SkillLinkTests(Fixture):
    """Point 5: one recorded link per skill in ~/.claude/skills."""

    def link(self, name):
        return self.home / ".claude/skills" / name

    def test_each_skill_gets_its_own_recorded_link(self):
        self.run_plan()
        self.assertEqual(
            os.readlink(self.link("notes")), str(self.home / agent_deploy.SKILLS / "notes")
        )
        self.assertIn(".claude/skills/notes", inventory(self.home)["paths"])
        # The plugin folder keeps the gateway and hooks, and holds no skills link.
        plugin = self.home / agent_deploy.TREE / "claude"
        self.assertFalse((plugin / "skills").exists() or (plugin / "skills").is_symlink())
        self.assertTrue((plugin / ".mcp.json").is_file())
        self.assertEqual(os.readlink(self.link("fleet")), str(plugin))

    def test_the_old_plugin_skills_link_goes_as_stale(self):
        old = f"{agent_deploy.TREE}/claude/skills"
        target = str(self.home / agent_deploy.SKILLS)
        (self.home / old).parent.mkdir(parents=True)
        (self.home / old).symlink_to(target)
        record = {"agent": "claude", "item": "skills", "fingerprint": {"link": target}}
        state = self.home / agent_deploy.INVENTORY
        state.write_text(
            json.dumps({"schema_version": agent_deploy.SCHEMA, "paths": {old: record}})
        )
        self.run_plan()
        self.assertFalse((self.home / old).is_symlink())
        self.assertNotIn(old, inventory(self.home)["paths"])

    def test_pulled_in_marketplace_skills_get_a_link_too(self):
        root = repository(
            Path(self.folder.name) / "market",
            {"skills/review/SKILL.md": "---\nname: review\ndescription: Review.\n---\n"},
        )
        pulled = agent_table.Item("skills", "review", {"path": "skills/review"}, root, "market")
        table = agent_table.load(self.checkout, self.manifest(), CATALOG)
        table.items.append(pulled)
        result = agent_deploy.plan(self.home, self.checkout, self.profile, table, CATALOG)
        self.assertEqual(
            result.outputs[".claude/skills/review"].link,
            str(self.home / agent_deploy.SKILLS / "review"),
        )

    def test_a_skill_named_fleet_fails_validation(self):
        checkout = repository(
            Path(self.folder.name) / "named-fleet",
            {".agents/skills/fleet/SKILL.md": "---\nname: fleet\ndescription: X.\n---\n"},
        )
        with self.assertRaisesRegex(ValueError, "skills.fleet: the name fleet is reserved"):
            agent_table.load(checkout, tomllib.loads(MANIFEST), CATALOG)

    def test_a_users_own_skill_link_stays(self):
        mine = self.link("notes")
        mine.parent.mkdir(parents=True)
        mine.mkdir()
        (mine / "SKILL.md").write_text("Mine.\n")
        result = self.run_plan()
        self.assertEqual((mine / "SKILL.md").read_text(), "Mine.\n")
        self.assertIn(
            "claude: ~/.claude/skills/notes holds your own file;"
            " Fleet leaves it and stopped managing it.",
            result.steps,
        )
        self.assertNotIn(".claude/skills/notes", inventory(self.home)["paths"])

    def test_a_users_own_skill_folder_gets_a_link_too(self):
        # Review F-025, defect 1: a skill the user placed in ~/.agents/skills, outside content.
        mine = self.home / agent_deploy.SKILLS / "my-own"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("---\nname: my-own\ndescription: Mine.\n---\n")
        self.run_plan()
        self.assertEqual(os.readlink(self.link("my-own")), str(mine))
        self.assertIn(".claude/skills/my-own", inventory(self.home)["paths"])
        # A content skill turned off stays off, even while its folder still exists.
        (self.home / agent_deploy.SKILLS / "notes").mkdir(parents=True, exist_ok=True)
        self.profile["agent_items"] = {"skills.notes": "off"}
        self.run_plan()
        self.assertFalse(self.link("notes").is_symlink())
        self.assertTrue(self.link("my-own").is_symlink())

    def test_an_excluded_or_removed_skill_has_no_link(self):
        self.run_plan()
        self.profile["agent_items"] = {"skills.notes": "off"}
        self.run_plan()
        self.assertFalse(self.link("notes").is_symlink())
        self.profile.pop("agent_items")
        self.run_plan()
        self.assertTrue(self.link("notes").is_symlink())
        # A domain limit that leaves this machine out removes the link too.
        self.profile["domain"] = "core"
        self.run_plan(MANIFEST + '[agents.skills.notes]\ndomains = ["work"]\n')
        self.assertFalse(self.link("notes").is_symlink())


LOCAL = PLUGINS.replace(
    'gemini = { id = "https://github.com/example/review-kit" }',
    'gemini = { id = "/opt/extensions/review-kit" }',
)


class LocalExtensionTests(PluginTests):
    """Point 8: a local Gemini CLI source installs nothing and becomes a manual step."""

    def test_a_local_source_is_a_manual_step_each_run_until_installed(self):
        line = (
            "gemini: install /opt/extensions/review-kit yourself:"
            " gemini extensions install /opt/extensions/review-kit"
        )
        result = self.deploy(LOCAL)
        self.assertEqual(self.calls(), ["codex plugin add review-kit@example"])
        self.assertIn(line, result.notes)
        self.assertEqual({record["agent"] for record in self.plugins()}, {"codex"})
        self.assertIn(line, self.deploy(LOCAL).notes)
        # Once the user installs it, the plugin is the user's (point 9).
        (self.home / ".gemini/extensions/review-kit").mkdir(parents=True)
        result = self.deploy(LOCAL)
        self.assertNotIn(line, result.notes)
        self.assertEqual(self.calls(), [])
        self.deploy("[agents]\nschema = 1\n")
        self.assertNotIn("gemini extensions uninstall review-kit", self.calls())

    def test_local_means_a_path_prefix_or_an_existing_folder(self):
        for source in ("/x", "~/x", "./x", "../x"):
            self.assertTrue(agent_deploy.local_source(source), source)
        self.assertTrue(agent_deploy.local_source(self.folder.name))
        for source in ("https://github.com/example/x", "git@github.com:example/x.git"):
            self.assertFalse(agent_deploy.local_source(source), source)


class UninstallPluginTests(PluginTests):
    """Point 11: uninstall removes the plugins that Fleet installed, and nothing else."""

    def test_uninstall_removes_recorded_installs_only(self):
        self.deploy()
        self.calls()
        records = agent_deploy.uninstall_plugins_plan(self.home, self.profile)
        self.assertEqual({record["agent"] for record in records}, {"codex", "gemini"})
        account = type("Account", (), {"applied_software": set()})()
        agent_deploy.uninstall_plugins(records, CATALOG, account)
        self.assertEqual(
            sorted(self.calls()),
            ["codex plugin remove review-kit@example", "gemini extensions uninstall review-kit"],
        )
        self.assertEqual(
            account.applied_software, {"codex plugin review-kit", "gemini plugin review-kit"}
        )

    def test_a_local_exception_keeps_the_plugin(self):
        self.deploy()
        self.profile["agent_items"] = {"plugins.review-kit": "local"}
        self.assertEqual(agent_deploy.uninstall_plugins_plan(self.home, self.profile), [])

    def test_a_failed_or_missing_command_prints_one_line_and_continues(self):
        self.deploy()
        self.calls()
        Path(self.folder.name, "fail-codex").touch()
        records = agent_deploy.uninstall_plugins_plan(self.home, self.profile)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            agent_deploy.uninstall_plugins(records, CATALOG)
        self.assertIn("codex plugin review-kit stays:", output.getvalue())
        self.assertIn("gemini extensions uninstall review-kit", self.calls())
        (self.bin / "gemini").unlink()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            agent_deploy.uninstall_plugins(records, CATALOG)
        self.assertIn("gemini plugin review-kit stays: gemini is not on PATH.", output.getvalue())

    def test_remove_fleet_reviews_and_runs_the_removals(self):
        import command

        self.deploy()
        self.calls()
        captured = {}

        def transaction(home, operations, label, *arguments, software=(), before_files=None):
            captured["rows"] = software
            account = type("Account", (), {"applied_software": set()})()
            before_files(account)
            captured["applied"] = account.applied_software
            return 0

        (self.home / ".fleet/profile.toml").write_text('domain = "work"\n')
        with (
            patch.object(command, "transaction", side_effect=transaction),
            patch.object(command, "managed_paths", return_value=[]),
            patch.object(command.enrollment, "agent_manifest", return_value=CATALOG),
        ):
            self.assertEqual(command.remove_fleet(self.home), 0)
        self.assertEqual(
            {row["name"] for row in captured["rows"]},
            {"codex plugin review-kit", "gemini plugin review-kit"},
        )
        self.assertTrue(all(row["action"] == "removed" for row in captured["rows"]))
        self.assertEqual(len(captured["applied"]), 2)
        # Uninstall never removes a marketplace, so other plugins from it stay.
        self.assertFalse([call for call in self.calls() if "marketplace" in call])


# The imported tests run in their own modules only.
for name in [name for name in dir(PluginTests) if name.startswith("test_")]:
    setattr(LocalExtensionTests, name, None)
    setattr(UninstallPluginTests, name, None)
del PluginTests

if __name__ == "__main__":
    unittest.main()
