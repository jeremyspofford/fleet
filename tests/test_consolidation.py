"""Keep consolidated commands, saved tool data, and local status aligned.

Usage: python3 -m unittest discover -s tests -v.
"""

import argparse
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import command
from test_style import command_goldens

ROOT = Path(__file__).resolve().parents[1]


class ConsolidationTests(unittest.TestCase):
    def output(self, arguments):
        with (
            patch.object(command, "current_command", return_value="fleet"),
            contextlib.redirect_stderr(io.StringIO()) as error,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = command.main(arguments)
        return result, output.getvalue() + error.getvalue()

    def test_removed_forms_name_their_successor_without_side_effects(self):
        for old, successor in (
            (["rollback"], "backups restore"),
            (["pins", "set", "--tool", "node"], "tools"),
            (["profile", "show"], "status"),
            (["content", "show"], "content"),
        ):
            for arguments in (old, ["help", *old[:1]] if old[0] != "content" else old):
                with self.subTest(arguments=arguments), patch.object(command, "profile") as profile:
                    result, text = self.output(arguments)
                    self.assertEqual(result, 2)
                    self.assertEqual(text, "Error: Use fleet " + successor + ".\n")
                    profile.assert_not_called()

    def sandbox(self):
        if not hasattr(self, "_sandbox"):
            folder = tempfile.TemporaryDirectory()
            self.addCleanup(folder.cleanup)
            self._sandbox = folder.name
        return self._sandbox

    def both_forms(self, *arguments):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/command.py"), *arguments],
            # Never read or write the real home's Fleet state from a test.
            env=dict(os.environ, HOME=self.sandbox()),
            text=True,
            capture_output=True,
        )
        return result.returncode, result.stdout + result.stderr

    def assert_same_command(self, other, name, action):
        parser = command.parser()
        for arguments in ([other, action], ["help", other], [other, "--help"]):
            with self.subTest(arguments=arguments):
                canonical = command.command_arguments.canonical(arguments)
                self.assertEqual(canonical, [name if item == other else item for item in arguments])
                self.assertEqual(self.both_forms(*arguments), self.both_forms(*canonical))
        args = parser.parse_args(command.command_arguments.canonical([other, action]))
        self.assertEqual((args.command, getattr(args, "action", action)), (name, action))

    def test_addon_reaches_addons(self):
        self.assert_same_command("addon", "addons", "list")

    def test_agent_reaches_agents(self):
        self.assert_same_command("agent", "agents", "list")

    def test_backup_reaches_backups(self):
        self.assert_same_command("backup", "backups", "list")

    def test_domains_reaches_domain(self):
        self.assert_same_command("domains", "domain", "list")

    def test_group_reaches_groups(self):
        self.assert_same_command("group", "groups", "list")

    def test_log_reaches_logs(self):
        self.assert_same_command("log", "logs", "list")

    def test_machine_reaches_machines(self):
        self.assert_same_command("machine", "machines", "list")

    def test_marketplace_reaches_marketplaces(self):
        self.assert_same_command("marketplace", "marketplaces", "list")

    def test_secret_reaches_secrets(self):
        self.assert_same_command("secret", "secrets", "list")

    def test_tool_reaches_tools(self):
        self.assert_same_command("tool", "tools", "list")

    def test_every_other_form_names_a_command_and_help_shows_only_canonical_names(self):
        names = set(command.DESCRIPTIONS)
        self.assertTrue(set(command.command_arguments.OTHER_FORMS.values()) <= names)
        self.assertFalse(set(command.command_arguments.OTHER_FORMS) & names)
        code, text = self.both_forms("--help")
        self.assertEqual(code, 0)
        for other in command.command_arguments.OTHER_FORMS:
            self.assertNotRegex(text, rf"(?m)^\s+{other}\s")

    def test_unknown_command_name_still_errors(self):
        for name in ("machinez", "toolz", "groupss"):
            with self.subTest(name=name):
                code, text = self.both_forms(name, "list")
                self.assertEqual(code, 2)
                self.assertIn(f"Unknown choice '{name}'", text)

    def test_subject_tool_and_restore_group_use_existing_engine_fields(self):
        parser = command.parser()
        for arguments, tool, version in (
            (["tools", "set", "node", "--version", "26.13.1"], "node", "26.13.1"),
            (["tools", "remove", "node"], "node", None),
        ):
            args = command.command_arguments.normalize(parser.parse_args(arguments), parser)
            self.assertEqual(args.tool, tool)
            self.assertEqual(args.pin, tool + "@" + version if version else tool)
        args = parser.parse_args(["backups", "restore", "--backup", "saved"])
        self.assertEqual(args.backup, "saved")
        self.assertEqual(parser.parse_args(["content"]).command, "content")
        names = next(
            item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
        ).choices
        # Agents, marketplaces, and dotfiles joined the 19; capture is the 23rd.
        self.assertEqual(len(names), 23)
        self.assertTrue({"backups", "tools", "status", "content"}.issubset(names))
        self.assertFalse({"backup", "rollback", "pins", "profile"}.intersection(names))

    def test_installer_tool_flag_keeps_legacy_data_and_hides_the_old_flag(self):
        import manager

        args = manager.parse_arguments(["--tool", "node=26.13.1"])
        old = manager.parse_arguments(["--pin", "node=26.13.1"])
        self.assertEqual(args.pin, [("node", "26.13.1")])
        self.assertEqual(old.pin, args.pin)
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            manager.parse_arguments(["--help"])
        self.assertNotIn("--pin", output.getvalue())
        self.assertIn("--tool", output.getvalue())

    def test_status_contains_agents_and_cached_updates_inside_its_fields(self):
        status = command_goldens()["status"]
        self.assertIn("  Tool node — 26.13.1 · installed: 26.3.1, 26.13.1\n", status)
        self.assertIn("  Agents             codex off\n", status)
        self.assertIn("  Updates            none (checked 09:02)\n", status)
        self.assertNotIn("\nUpdates:", status)
        self.assertIn("~/.fleet/profile.toml", status)
        self.assertFalse(re.search(r"\b(?:pin|pins|pinned|unpin)\b", status, re.I))


if __name__ == "__main__":
    unittest.main()
