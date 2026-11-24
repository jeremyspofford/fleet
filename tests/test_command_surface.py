"""Keep action groups, help spellings, and terminal menu coverage aligned.

Usage: python3 -m unittest discover -s tests -v.
"""

import contextlib
import io
import os
import re
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import command
import command_arguments
import tui_actions
from tui_navigation import ACTIONS, AREAS, RELATED


class CommandSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def call(self, arguments, expected=0):
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            try:
                code = command.main(arguments)
            except SystemExit as stopped:
                code = stopped.code
        self.assertEqual(code, expected, output.getvalue() + error.getvalue())
        return output.getvalue() + error.getvalue()

    def test_every_help_spelling_at_every_command_and_action(self):
        self.assertEqual(self.call([]), self.call(["help"]))
        paths = [[], *[[name] for name in command.DESCRIPTIONS if name != "help"]]
        paths += [
            [name, action]
            for name, actions in command_arguments.GROUPS.items()
            for action in actions
        ]
        for path in paths:
            with self.subTest(path=path):
                expected = self.call(path + ["--help"])
                for arguments in (path + ["-h"], path + ["help"], ["help", *path]):
                    self.assertEqual(self.call(arguments), expected)
                self.assertNotIn("Usage: Usage:", expected)
                for line in expected.splitlines():
                    self.assertLessEqual(len(line), 100)
        self.assertEqual(self.call(["help", "--help"]), self.call(["--help"]))
        self.assertEqual(list(self.home.rglob("*")), [])

    def test_help_lists_commands_actions_and_flags_alphabetically(self):
        output = self.call(["help"])
        names = re.findall(r"^    ([a-z][a-z-]+)\s", output, re.M)
        self.assertEqual(names, sorted(command.DESCRIPTIONS))
        paths = [[], *[[name] for name in command.DESCRIPTIONS]]
        for group, actions in command_arguments.GROUPS.items():
            output = self.call([group])
            names = re.findall(r"^    ([a-z][a-z-]+)\s", output, re.M)
            self.assertEqual(names, sorted(actions))
            paths.extend([group, action] for action in actions)
        for path in paths:
            output = self.call([*path, "--help"])
            flags = re.findall(r"^  (?:-h, )?--([a-z][a-z-]+)\b", output, re.M)
            self.assertEqual(flags, sorted(flags), path)
        for script in ("install.sh", "scripts/generate-agent-configs.sh"):
            output = subprocess.check_output(
                ["bash", str(command.ROOT / script), "--help"], text=True
            )
            flags = re.findall(r"^  (?:-h, )?--([a-z][a-z-]+)\b", output, re.M)
            self.assertEqual(flags, sorted(flags), script)

    def test_bare_groups_show_actions_without_a_profile(self):
        for group, actions in command_arguments.GROUPS.items():
            with self.subTest(group=group):
                output = self.call([group])
                self.assertEqual(output, self.call([group, "--help"]))
                for action in actions:
                    self.assertIn(action, output)
        for arguments in (
            ["domain", "--name", "personal"],
            ["groups", "--name", "docker", "--state", "off"],
            ["status", "--check"],
        ):
            self.call(arguments, expected=2)
        self.assertEqual(list(self.home.rglob("*")), [])

    def test_version_spellings_print_the_same_version_without_enrollment(self):
        output = self.call(["version"])
        self.assertEqual(output, self.call(["--version"]))
        self.assertIn((command.ROOT / "VERSION").read_text().strip(), output)
        self.assertNotIn("Content commit", output)

    def test_forms_collect_named_arguments_and_cancel_without_dispatch(self):
        cases = [
            (
                "pin-set",
                {"tool": "node", "version": "24.14.1"},
                ["tools", "set", "node", "--version", "24.14.1"],
            ),
            ("pin-remove", {"tool": "python"}, ["tools", "remove", "python"]),
            ("machine-export", {"output": ""}, ["machines", "export"]),
            (
                "machine-export",
                {"output": "~/machine.toml"},
                ["machines", "export", "--output", "~/machine.toml"],
            ),
            (
                "name-connection",
                {"name": "", "target": "mini-pc"},
                ["machines", "set-ssh", "--target", "mini-pc"],
            ),
        ]
        with patch.object(command, "profile", return_value={"pins": {"node": "24.14.1"}}):
            for action, response, arguments in cases:
                with self.subTest(action=action, response=response):
                    with patch.object(tui_actions.ui_channel, "send", return_value=response):
                        self.assertEqual(tui_actions.arguments(action, self.home), arguments)
                    with patch.object(tui_actions.ui_channel, "send", return_value=None):
                        self.assertIsNone(tui_actions.arguments(action, self.home))
        with patch.object(
            tui_actions.ui_channel,
            "send",
            side_effect=[{"choice": "machines"}, {"choice": "set-ssh"}],
        ):
            self.assertEqual(
                tui_actions.arguments("command-help", self.home), ["help", "machines", "set-ssh"]
            )
        with patch.object(tui_actions.ui_channel, "send", return_value={"unexpected": "yes"}):
            with self.assertRaisesRegex(ValueError, "Invalid form response"):
                tui_actions.arguments("pin-set", self.home)
        self.assertEqual(list(self.home.rglob("*")), [])

    def test_every_primary_command_output_has_a_golden(self):
        # Mutations snapshot their completed reviews; screens and the wizard also test their input views.
        expected = {
            "agents": "views/agents.txt",
            "marketplaces": "views/marketplaces.txt",
            "logs": "views/logs.txt",
            "topology": "topology.txt",
            "tui": "tui/dashboard-80x24.txt",
            "check": "views/check.txt",
            "backups": "views/backups.txt",
            "secrets": "views/secrets.txt",
            "content": "views/content.txt",
            "machines": "views/machines.txt",
            "addons": "views/addons.txt",
            "update": "views/update review.txt",
            "status": "views/status.txt",
            "diff": "views/diff changed.txt",
            "domain": "views/domain.txt",
            "dotfiles": "views/dotfiles.txt",
            "groups": "views/groups.txt",
            "tools": "views/tools.txt",
            "configure": "views/configure review.txt",
            "capture": "views/capture review.txt",
            "doctor": "views/doctor.txt",
            "version": "views/version.txt",
            "uninstall": "views/uninstall review.txt",
        }
        self.assertEqual(set(expected), set(command.DESCRIPTIONS))
        for name, path in expected.items():
            self.assertTrue((command.ROOT / "tests/fixtures" / path).read_text().strip(), name)

    def test_every_cli_command_and_group_action_has_a_reachable_menu_route(self):
        reachable = {item for items in AREAS.values() for item in items}
        while True:
            expanded = reachable | {
                child for parent in reachable for child in RELATED.get(parent, ())
            }
            if expanded == reachable:
                break
            reachable = expanded
        self.assertEqual(reachable, set(ACTIONS))
        import argparse

        parser = command.parser()
        subcommands = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        ).choices
        commands = {ACTIONS[action][1][0] for action in reachable} - {"help"}
        self.assertEqual(commands, set(subcommands))
        expected = {
            (name, action)
            for name, child in subcommands.items()
            for subparser in child._actions
            if isinstance(subparser, argparse._SubParsersAction)
            for action in subparser.choices
        }
        available = {
            tuple(args[:2]) for _, args in ACTIONS.values() if args[0] in command_arguments.GROUPS
        }
        self.assertEqual(expected, available)


if __name__ == "__main__":
    unittest.main()
