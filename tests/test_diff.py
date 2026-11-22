"""Diff prints patches without writing enrollment state or backups.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_diff.py --help.
"""

import json
import os
from pathlib import Path
import unittest

import test_command as helpers
from test_manager import command, ROOT


class DiffTests(unittest.TestCase):
    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli

    def snapshot(self):
        return {
            str(path.relative_to(self.home)): ("link", os.readlink(path))
            if path.is_symlink()
            else ("file", path.read_bytes(), path.stat().st_mode)
            if path.is_file()
            else ("directory",)
            for path in self.home.rglob("*")
        }

    def test_clean_changed_and_stat_are_read_only(self):
        self.enroll()
        before = self.snapshot()
        clean = self.cli("diff")
        sha = command("git", "-C", str(self.content), "rev-parse", "HEAD").strip()[:8]
        self.assertEqual(clean, f"No differences. Your files match {self.content} @ {sha}.\n")
        self.assertEqual(
            clean.replace(str(self.content), "https://example.invalid/dotfiles").replace(
                sha, "12345678"
            ),
            (ROOT / "tests/fixtures/views/diff clean.txt").read_text(),
        )
        self.assertEqual(self.snapshot(), before)
        Path("/tmp/dotfleet-diff-clean.txt").write_text(clean)
        (self.home / ".file").write_text("local change\n")
        before = self.snapshot()
        changed = self.cli("diff", expected=1)
        self.assertIn("~/.file\n", changed)
        self.assertIn("-local change\n", changed)
        self.assertIn("+original\n", changed)
        self.assertNotIn("Locations", changed)
        self.assertNotIn("Selected choices", changed)
        self.assertEqual(changed, (ROOT / "tests/fixtures/views/diff changed.txt").read_text())
        self.assertEqual(self.snapshot(), before)
        Path("/tmp/dotfleet-diff-changed.txt").write_text(changed)
        self.assertEqual(self.cli("diff", "--stat", expected=1), "~/.file\n")
        self.assertEqual(self.snapshot(), before)

    def old_mise_layout(self):
        """Make this machine look enrolled before PR #80: Fleet's pins in the global config.toml."""
        import manager

        fragment, user = ".config/mise/conf.d/fleet.toml", ".config/mise/config.toml"
        source = self.home / ".fleet/chezmoi-source"
        old = (self.home / fragment).read_text()
        (source / "dot_config/mise/conf.d/fleet.toml").unlink()
        (self.home / fragment).unlink()
        state = ["chezmoi", "state", "delete", "--bucket=entryState"]
        command(*state, "--key=" + str(self.home / fragment), env=self.env, cwd=self.home)
        (source / "dot_config/mise/config.toml").write_text(old)
        apply = ["chezmoi", "apply", "--force", str(self.home / user)]
        command(*apply, env=self.env, cwd=self.home)
        outputs = self.home / ".fleet/outputs.json"
        recorded = json.loads(outputs.read_text())
        recorded.pop(fragment)
        recorded[user] = manager.fingerprint(self.home / user)
        outputs.write_text(json.dumps(recorded))

    def test_diff_and_doctor_work_before_the_first_mise_handover(self):
        # Report F-033: diff stopped with "config.toml: not managed" on machines enrolled before #80.
        self.enroll()
        self.old_mise_layout()
        (self.home / ".file").write_text("local change\n")
        before = self.snapshot()
        changed = self.cli("diff", expected=1)
        self.assertNotIn("not managed", changed)
        self.assertNotIn("Preview failed", changed)
        self.assertIn("~/.file\n", changed)
        self.assertIn("~/.config/mise/config.toml\n", changed)
        self.assertIn("~/.config/mise/conf.d/fleet.toml\n", changed)
        stat = self.cli("diff", "--stat", expected=1)
        self.assertIn("~/.config/mise/config.toml\n", stat)
        self.assertEqual(self.snapshot(), before)
        doctor = self.cli("doctor", expected=1)
        self.assertRegex(doctor, r"Drifted files +~/\.file\n")

    def test_update_preview_includes_removed_file_and_generated_identity(self):
        self.enroll()
        (self.content / ".extra").unlink()
        command("git", "-C", str(self.content), "add", ".")
        command(
            "git",
            "-C",
            str(self.content),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fix: retire extra file",
        )
        (self.home / ".config/git/local.gitconfig").write_text("[user]\nname = Changed\n")
        before = self.snapshot()
        output = self.cli("diff", expected=1)
        self.assertIn("~/.extra\n", output)
        self.assertIn("deleted file mode", output)
        self.assertIn("-group\n", output)
        self.assertIn("~/.config/git/local.gitconfig\n", output)
        self.assertIn("Command Example", output)
        self.assertEqual(self.snapshot(), before)


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
