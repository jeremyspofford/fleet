"""Check that instructions name the available command and software names the product.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_command_display.py --help.
"""

import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_command as commands
import test_manager as helpers
from test_manager import command, repository
from render_run import render
import updates


class CommandDisplayTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    install = helpers.EnrollmentTests.install
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record

    def enroll_helpers(self):
        content = repository(
            self.base / "content",
            {
                "fleet.toml": (
                    'format = "plain"\n[groups.helpers]\n'
                    'description = "Fleet helper alias"\ndefault = true\nfiles = [".helpers"]\n'
                ),
                ".aliases": '[ ! -r "$HOME/.helpers" ] || source "$HOME/.helpers"\n',
                ".helpers": (
                    "if command -v dotfleet >/dev/null 2>&1 "
                    "&& ! command -v fleet >/dev/null 2>&1; then\n"
                    "    alias fleet='dotfleet'\nfi\n"
                ),
                ".zshrc": 'source "$HOME/.aliases"\n',
                ".bashrc": 'source "$HOME/.aliases"\n',
            },
        )
        output = self.install(content)
        self.exe = self.home / ".local/bin/dotfleet"
        self.env["PATH"] = str(self.home / ".local/bin") + ":" + self.env["PATH"]
        return output

    def assert_instructions(self, output, expected):
        self.assertIn("Update    " + expected + " update", output)
        self.assertIn("Wizard    " + expected + " configure", output)
        record, _ = self.record()
        self.assertEqual(record["instruction_commands"]["after"], expected)
        self.assertEqual(render(record), output[output.index("1 · Software") :])
        version = (Path(helpers.ROOT) / "VERSION").read_text().strip()
        software = output.split("2 · Manager and content")[0]
        self.assertRegex(software, r"Fleet\s+" + version.replace(".", r"\."))
        self.assertIn("~/.local/bin/dotfleet", output)
        self.assert_quiet_status()

    def assert_unchecked_update_hint(self, expected):
        """Assert the hint for a home with no saved update check.

        Enrollment ends with a check. It saves a result when the fixture upstream answers,
        so remove that result first. The test then does not depend on that check.
        """
        (self.home / updates.CACHE).unlink(missing_ok=True)
        hint = f"Updates           not checked yet — run: {expected} check"
        self.assertIn(hint, self.cli("status"))

    def assert_cached_update_hint(self, expected):
        path = self.home / updates.CACHE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checked_at": "2026-11-16T12:00:00+00:00",
                    "sources": [
                        {"name": "manager", "behind": 3},
                        {"name": "content", "behind": 1},
                    ],
                }
            )
        )
        self.assertIn("— run: " + expected + " update", self.cli("status"))
        path.unlink()

    def test_helpers_use_alias_in_review_hints_and_help(self):
        self.assert_instructions(self.enroll_helpers(), "fleet")
        self.assert_unchecked_update_hint("fleet")
        self.assert_cached_update_hint("fleet")
        self.assertIn(
            "Use fleet domain set, tools set, groups set, or configure.", self.cli("status")
        )
        self.assertIn("Usage: fleet check", self.cli("check", "--help"))
        self.assertIn("fleet domain set --name NAME", self.cli("domain", "list"))
        self.assertIn("Run fleet configure", self.cli("configure", expected=1))
        alias = command("bash", "-c", 'source "$HOME/.aliases"; alias fleet', env=self.env)
        self.assertEqual(alias, "alias fleet='dotfleet'\n")
        # The stored review keeps its command choice when rendered on another machine.
        recorded, _ = self.record()
        other = self.base / "other-machine"
        other.mkdir()
        (other / "fleet").write_text("#!/bin/sh\nexit 0\n")
        (other / "fleet").chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(other)}):
            self.assertIn("Update    fleet update", render(recorded))
        disabled = self.cli("groups", "set", "--name", "helpers", "--state", "off")
        self.assertIn("Update    dotfleet update", disabled)
        self.assertIn("Wizard    dotfleet configure", disabled)
        self.assert_unchecked_update_hint("dotfleet")
        self.assert_cached_update_hint("dotfleet")
        self.assertIn(
            "Use dotfleet domain set, tools set, groups set, or configure.",
            self.cli("status"),
        )
        self.assert_quiet_status()

    def test_real_fleet_binary_keeps_dotfleet_instructions(self):
        executable = self.base / "tools/fleet"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "Rancher fixture"\n')
        executable.chmod(0o755)
        self.assert_instructions(self.enroll_helpers(), "dotfleet")
        self.assert_unchecked_update_hint("dotfleet")
        self.assert_cached_update_hint("dotfleet")
        self.assertIn("Usage: dotfleet check", self.cli("check", "--help"))
        self.assertIn(
            "Use dotfleet domain set, tools set, groups set, or configure.",
            self.cli("status"),
        )
        selected = command("bash", "-c", 'source "$HOME/.aliases"; command -v fleet', env=self.env)
        self.assertEqual(selected.strip(), str(executable))


if __name__ == "__main__":
    unittest.main()
