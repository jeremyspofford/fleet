"""Check complete change accounts from actual enrollment runs and saved evidence.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_run_account.py --help.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_manager as helpers
from test_manager import ROOT, command, repository
from common import toml
from run_account import profile_changes


def summary(output):
    output = output.replace("\r\n", "\n")
    return output[output.rindex("1 · Software") :]


def category(text, number):
    start = text.index(f"{number} · ")
    return text[start : text.index(f"{number + 1} · ", start)] if number < 6 else text[start:]


class RunAccountTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status

    def enroll(self, repo=None, extra=(), root=ROOT):
        flags = [
            "--defaults",
            "--domain",
            "core",
            "--git-name",
            "Audit Example",
            "--git-email",
            "audit@example.invalid",
            "--agents",
            "none",
        ]
        if repo:
            flags += ["--repo", str(repo)]
        return command(
            "bash", str(root / "install.sh"), *flags, *extra, env=self.env, cwd=self.home
        )

    def fixture(self):
        return repository(
            self.base / "content",
            {
                ".changed": "before\n",
                ".removed": "optional file\n",
                "dotfleet.toml": 'format = "plain"\n[groups.extra]\ndescription="An optional example file"\nfiles=[".removed"]\n',
            },
        )

    def test_file_changes_match_diff_and_backup_records(self):
        repo = self.fixture()
        self.enroll(repo)
        old = command("git", "-C", str(repo), "rev-parse", "HEAD").strip()
        (repo / ".changed").write_text("after\n")
        (repo / ".added").write_text("new file\n")
        command("git", "-C", str(repo), "add", ".")
        command(
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fix: update test content",
        )
        new = command("git", "-C", str(repo), "rev-parse", "HEAD").strip()
        text = summary(self.enroll(extra=["--group", "extra=off"]))
        self.assertIn("  + ~/.added\n", text)
        self.assertIn("  ~ ~/.changed\n", category(text, 3))
        self.assertIn("  − ~/.removed\n", text)
        self.assertIn("Group extra  on → off", text)
        self.assertIn(f"Content  {old[:8]} → {new[:8]} · ~/.fleet/dotfiles/content", text)
        latest = sorted((self.home / ".fleet/backups").iterdir())[-1]
        record = json.loads((latest / "RUN.json").read_text())
        from render_run import render

        self.assertEqual(text, render(record))
        self.assertEqual(
            record["backup_manifest"], json.loads((latest / "MANIFEST.json").read_text())
        )
        planned = {item["relative"]: item["action"] for item in record["files"]}
        self.assertEqual(
            {name: planned[name] for name in [".added", ".changed", ".removed"]},
            {".added": "added", ".changed": "changed", ".removed": "removed"},
        )
        diff = (latest / "CHEZMOI-DIFF.patch").read_text()
        self.assertIn(".added", diff)
        self.assertIn(".changed", diff)
        self.assertEqual((latest / "home/.changed").read_text(), "before\n")
        self.assertEqual((latest / "retired/.removed").read_text(), "optional file\n")
        self.assertEqual((latest / "RUN.json").stat().st_mode & 0o777, 0o600)
        self.assert_quiet_status()
        if os.environ.get("DOTFLEET_TEST_REPORT"):
            Path(os.environ["DOTFLEET_TEST_REPORT"]).write_text(text)

    def test_no_change_run_is_compact_with_recorded_locations_and_commands(self):
        self.enroll(self.fixture())
        text = summary(self.enroll())
        for number in range(1, 5):
            self.assertIn("no changes.", category(text, number))
        self.assertEqual(category(text, 3).strip(), "3 · Files — no changes.")
        self.assertIn("Already present:", category(text, 1))
        self.assertEqual(category(text, 5).strip(), "5 · Locations — recorded in RUN.json.")
        self.assertEqual(len([line for line in text.splitlines() if line]), 12)
        record = json.loads(
            sorted((self.home / ".fleet/backups").glob("*/RUN.json"))[-1].read_text()
        )
        self.assertEqual(
            record["locations"]["Profile file"], str(self.home / ".fleet/profile.toml")
        )
        self.assertIn("MANIFEST.json", category(text, 6))
        self.assertIn("dotfleet configure", category(text, 6))
        self.assert_quiet_status()

    def test_manager_update_uses_last_confirmed_commit(self):
        files = {
            "install.sh": (ROOT / "install.sh").read_text(),
            "VERSION": (ROOT / "VERSION").read_text(),
            "requirements-tui.txt": (ROOT / "requirements-tui.txt").read_text(),
            "scripts/tui.tcss": (ROOT / "scripts/tui.tcss").read_text(),
        }
        files.update(
            {
                str(path.relative_to(ROOT)): path.read_text()
                for path in (ROOT / "scripts").glob("*.py")
            }
        )
        files.update(
            {
                str(path.relative_to(ROOT)): path.read_text()
                for path in (ROOT / "scripts").glob("*.sh")
            }
        )
        files["agents/manifest.toml"] = (ROOT / "agents/manifest.toml").read_text()
        manager = repository(self.base / "manager", files)
        self.enroll(self.fixture(), root=manager)
        old = command("git", "-C", str(manager), "rev-parse", "HEAD").strip()
        (manager / "VERSION").write_text("0.2.1\n")
        command("git", "-C", str(manager), "add", ".")
        command(
            "git",
            "-C",
            str(manager),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fix: record a manager update",
        )
        new = command("git", "-C", str(manager), "rev-parse", "HEAD").strip()
        text = summary(self.enroll(root=manager))
        self.assertIn(f"{old[:8]} → {new[:8]}", text)
        self.assertIn("~/.fleet/manager", text)
        self.assertEqual(category(text, 2).count("→"), 1)

    def test_review_has_full_future_account_and_cancel_has_no_changes(self):
        repo = self.fixture()
        self.enroll(repo)
        snapshot = {
            str(path.relative_to(self.home)): (
                os.readlink(path) if path.is_symlink() else path.read_bytes()
            )
            for path in self.home.rglob("*")
            if (path.is_file() or path.is_symlink()) and ".fleet/runs/" not in str(path)
        }
        answers = [
            (b"Content source [", b"repo\n"),
            (b"Content repository [", b"\n"),
            (b"Domain [core]:", b"personal\n"),
            (b"Git author name [", b"\n"),
            (b"Git author email [", b"\n"),
            (b"Tools [accept]:", b"\n"),
            (b"extra [on]:", b"off\n"),
            (b"Review [confirm]:", b"quit\n"),
        ]
        output = self.terminal(answers, flags=["--configure"], auto_wizard=False)
        review = output.split("9. Review before apply", 1)[1].split("Review [confirm]:", 1)[0]
        for number, label in enumerate(
            [
                "Software",
                "Manager and content",
                "Files",
                "Profile changes",
                "Locations",
                "Next steps",
            ],
            1,
        ):
            self.assertIn(f"{number} · {label}", review)
        self.assertIn("removed.", review)
        self.assertIn("− ~/.removed", review)
        self.assertIn("Group extra", review)
        self.assertIn("on → off", review)
        text = summary(output)
        for number in range(1, 5):
            self.assertIn("no changes.", category(text, number))
        after = {
            str(path.relative_to(self.home)): (
                os.readlink(path) if path.is_symlink() else path.read_bytes()
            )
            for path in self.home.rglob("*")
            if (path.is_file() or path.is_symlink()) and ".fleet/runs/" not in str(path)
        }
        self.assertEqual(set(after), set(snapshot))
        for name in snapshot:
            self.assertTrue(after[name] == snapshot[name], name)

    def test_failed_unattended_conflict_does_not_claim_file_changed(self):
        self.enroll(self.fixture())
        (self.home / ".changed").write_text("local edit\n")
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--defaults"],
            env=self.env,
            cwd=self.home,
            capture_output=True,
            text=True,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 1)
        text = summary(result.stdout)
        self.assertIn("chezmoi status: failed", text)
        self.assertNotIn("~/.changed", category(text, 3))
        self.assertEqual((self.home / ".changed").read_text(), "local edit\n")

    def test_pin_removal_lists_each_rendered_file_changed(self):
        repo = self.fixture()
        self.enroll(repo, extra=["--tool", "node=24.14.1"])
        answers = [
            (b"Tools [accept]:", b"edit\n"),
            (b"Tool versions [none]:", b"none\n"),
            (b"extra [on]:", b"\n"),
            (b"Review [confirm]:", b"confirm\n"),
        ]
        output = self.terminal(
            answers,
            flags=[
                "--configure",
                "--repo",
                str(repo),
                "--domain",
                "core",
                "--git-name",
                "Audit Example",
                "--git-email",
                "audit@example.invalid",
            ],
            auto_wizard=False,
        )
        text = summary(output)
        self.assertIn("Tool node  24.14.1 → not set", text)
        for name in [
            ".config/mise/conf.d/fleet.toml",
            ".config/chezmoi/chezmoi.toml",
            ".fleet/profile.toml",
        ]:
            self.assertIn("~ ~/" + name, category(text, 3))
        self.assert_quiet_status()

    def test_pin_removal_is_an_explicit_profile_change(self):
        self.assertEqual(
            profile_changes({"pins": {"node": "24.14.1"}}, {"pins": {}}),
            [{"name": "pins.node", "old": "24.14.1", "new": None}],
        )


if __name__ == "__main__":
    unittest.main()
