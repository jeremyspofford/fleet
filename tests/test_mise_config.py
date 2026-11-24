"""Generate Fleet's Mise fragment in a form Mise can edit, and leave config.toml to the user.

Mise edits a [tools] table in place. A quoted "tools" inline table makes mise use panic.
Fleet's pins live in ~/.config/mise/conf.d/fleet.toml. mise use -g writes config.toml.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_mise_config.py --help.
"""

import json
import shutil
import subprocess
import tomllib
import unittest

from common import toml, toml_tables
import test_manager as helpers
from test_manager import command

CONFIG = ".config/mise/conf.d/fleet.toml"
USER = ".config/mise/config.toml"


class MiseConfigTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    install = helpers.EnrollmentTests.install

    def config(self):
        return (self.home / CONFIG).read_text()

    def mise(self, *arguments):
        """Run the fixture's Mise offline against this home. Return the completed process."""
        location = shutil.which("mise", path=self.env["PATH"])
        if location is None:
            self.skipTest("Mise is not installed; CI installs it in the cold-install step.")
        return subprocess.run(
            [location, *arguments],
            env=dict(self.env, MISE_OFFLINE="1"),
            cwd=self.home,
            text=True,
            capture_output=True,
        )

    def assert_tables(self, text):
        lines = text.splitlines()
        self.assertIn("[tools]", lines)
        self.assertIn("[settings]", lines)
        self.assertFalse([line for line in lines if line.startswith(('"tools" =', '"settings" ='))])

    def assert_mise_use_succeeds(self):
        """Add a scratch pin with Mise's own editor. The system version downloads nothing."""
        before = tomllib.loads(self.config())["tools"]
        result = self.mise("use", "--path", str(self.home / CONFIG), "jq@system")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("panicked", result.stderr)
        after = tomllib.loads(self.config())["tools"]
        self.assertEqual(after.pop("jq"), "system")
        self.assertEqual(after, before)

    def test_writer_emits_table_headers_with_the_same_values(self):
        data = {
            "min_version": "2026.1.0",
            "tools": {
                "node": "26.13.1",
                "npm:@google/gemini-cli": "1.0.0",
                "python": ["3.12.13", "3.14.6"],
                "rust": {"version": "1.90.0", "profile": "minimal"},
            },
            "settings": {"experimental": False},
        }
        text = toml_tables(data)
        self.assert_tables(text)
        self.assertIn('\nnode = "26.13.1"\n', text)
        self.assertEqual(tomllib.loads(text), data)

    def test_generated_config_has_tables_and_mise_can_edit_it(self):
        self.install()
        self.assert_tables(self.config())
        self.assertIs(tomllib.loads(self.config())["settings"]["experimental"], False)
        self.assert_mise_use_succeeds()

    def old_layout(self):
        """Recreate what earlier Fleet applied: its pins in the global config.toml."""
        import manager

        rendered = tomllib.loads(self.config())
        old = toml_tables(rendered)
        source = self.home / ".fleet/chezmoi-source"
        (source / "dot_config/mise/conf.d/fleet.toml").unlink()
        (self.home / CONFIG).unlink()
        # Earlier Fleet never wrote the fragment, so chezmoi holds no memory of it.
        command(
            "chezmoi",
            "state",
            "delete",
            "--bucket=entryState",
            "--key=" + str(self.home / CONFIG),
            env=self.env,
            cwd=self.home,
        )
        (source / "dot_config/mise/config.toml").write_text(old)
        command("chezmoi", "apply", "--force", str(self.home / USER), env=self.env, cwd=self.home)
        outputs = self.home / ".fleet/outputs.json"
        recorded = json.loads(outputs.read_text())
        recorded.pop(CONFIG)
        recorded[USER] = manager.fingerprint(self.home / USER)
        outputs.write_text(json.dumps(recorded))
        return rendered

    def test_an_old_machine_moves_its_pins_and_keeps_its_own_mise_entries(self):
        self.install()
        # Record pins the way enrollment does, so the update must carry them through.
        saved = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(saved.read_text())
        profile["pins"] = {"node": "24.1.0", "python": "3.13.0"}
        saved.write_text(toml(profile))
        self.install(defaults=False)
        rendered = self.old_layout()
        # A local mise use -g edits the old Fleet-managed file, as in report F-007.
        result = self.mise("use", "-g", "jq@system")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = self.install(defaults=False)
        self.assertIn("Mise: ~/.config/mise/config.toml keeps only your own entries", output)
        self.assertEqual(tomllib.loads((self.home / USER).read_text()), {"tools": {"jq": "system"}})
        self.assert_tables(self.config())
        self.assertEqual(tomllib.loads(self.config()), rendered)
        after = tomllib.loads(saved.read_text())
        self.assertEqual(after["pins"], {"node": "24.1.0", "python": "3.13.0"})
        # The next mise use -g edits only the user's file. No Fleet run fails over it.
        before = self.config()
        result = self.mise("use", "-g", "shfmt@system")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.config(), before)
        self.install(defaults=False)
        self.assertEqual(
            tomllib.loads((self.home / USER).read_text()),
            {"tools": {"jq": "system", "shfmt": "system"}},
        )
        status = command("chezmoi", "status", env=self.env, cwd=self.home)
        self.assertEqual(status.strip(), "")

    def test_an_old_machine_without_own_entries_retires_the_old_file(self):
        self.install()
        self.old_layout()
        output = self.install(defaults=False)
        self.assertIn("Mise: Fleet's pins moved to ~/.config/mise/conf.d/fleet.toml.", output)
        self.assertFalse((self.home / USER).exists())
        backups = sorted((self.home / ".fleet/backups").glob("*/retired/" + USER))
        self.assertTrue(backups, "the old file stays in the run's backup")
        self.assertTrue((self.home / CONFIG).is_file())

    def test_an_interrupt_after_the_handover_leaves_every_tool_in_place(self):
        # Jeremy's run 20261124-093020: Ctrl-C after the handover left no global Mise tools.
        self.install()
        saved = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(saved.read_text())
        profile["pins"] = {"node": "24.1.0", "python": "3.13.0"}
        saved.write_text(toml(profile))
        self.install(defaults=False)
        rendered = self.old_layout()
        self.assertEqual(sorted(rendered["tools"]), ["node", "python"])
        real = shutil.which("chezmoi", path=self.env["PATH"])
        fake = self.base / "interrupting-bin"
        fake.mkdir()
        (fake / "chezmoi").write_text(
            "#!/usr/bin/env bash\n"
            "# Stop the apply that apply.sh starts, as Ctrl-C would. Pass every other call on.\n"
            'for argument in "$@"; do [[ $argument == --error-on-conflict ]] && exit 130; done\n'
            f'exec "{real}" "$@"\n'
        )
        (fake / "chezmoi").chmod(0o755)
        env = dict(self.env, PATH=f"{fake}:{self.env['PATH']}")
        # No terminal, so apply.sh takes the unattended branch that the fake stops.
        result = subprocess.run(
            ["bash", str(helpers.ROOT / "install.sh")],
            env=env,
            cwd=self.home,
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        # The handover ran: Fleet's pins left the global config before the stop.
        self.assertIn("Mise: Fleet's pins moved to ~/.config/mise/conf.d/fleet.toml.", output)
        self.assertFalse((self.home / USER).exists())
        listed = self.mise("ls", "--current")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        for tool in rendered["tools"]:
            self.assertIn(tool, listed.stdout, listed.stdout + listed.stderr)


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    argument_parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        usage()
