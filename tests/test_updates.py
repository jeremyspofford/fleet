"""Prove that checks and updates use the same remote branches and preserve cached failures.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_updates.py --help.
"""

import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import time
import tomllib
import unittest

import dotfiles_library
from unittest.mock import patch

import test_command as commands
from test_manager import command
from common import toml
from style import section
from installation import MANAGER
import updates


def advance(path, count, name="README.fixture", content="change"):
    for number in range(count):
        (path / name).write_text(f"{content} {number + 1}\n")
        command("git", "-C", str(path), "add", name)
        command(
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fix: fixture change",
        )
    return command("git", "-C", str(path), "rev-parse", "HEAD").strip()


class UpdateTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def paths(self):
        return self.home / MANAGER, dotfiles_library.active(self.home)

    def snapshot(self):
        return (
            (self.home / ".fleet/profile.toml").read_bytes(),
            sorted((self.home / ".fleet/backups").iterdir()),
            (self.home / ".fleet/registry.toml").read_bytes(),
            [(path, path.read_bytes()) for path in self.home.glob(".fleet/backups/*/RUN.json")],
        )

    def checked_time(self):
        record = json.loads((self.home / updates.CACHE).read_text())
        return datetime.datetime.fromisoformat(record["checked_at"]).astimezone().strftime("%H:%M")

    def test_behind_update_agreement_and_current(self):
        self.enroll()
        paths = self.paths()
        before = [command("git", "-C", str(path), "rev-parse", "HEAD").strip() for path in paths]
        targets = [advance(self.origin, 3), advance(self.content, 1, ".file", "updated")]
        untouched = self.snapshot()
        behind_check = self.cli("check", expected=1)
        self.assertEqual(
            behind_check,
            section("Check") + f"\n  Manager  3 commits behind @ {before[0][:8]}\n"
            f"  Content  1 commit behind @ {before[1][:8]}\n",
        )
        self.assertEqual(untouched, self.snapshot())
        cache = self.home / updates.CACHE
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        record = json.loads(cache.read_text())
        self.assertEqual([entry["target"] for entry in record["sources"]], targets)
        self.assertEqual([entry["current"] for entry in record["sources"]], before)
        self.assertEqual([entry["path"] for entry in record["sources"]], list(map(str, paths)))
        behind_status = self.cli("status")
        self.assertIn(
            f"Updates           manager +3, content +1 (checked {self.checked_time()}) — run: dotfleet update",
            behind_status,
        )
        # Local-only choices must not leak into update and silently suppress content pulls.
        self.env["DOTFLEET_LOCAL_CONTENT"] = "1"
        review = self.cli("update")
        self.assertIn("2 · Manager and content — 2 updated.", review)
        self.assertEqual((self.home / ".file").read_text(), "updated 1\n")
        for path, old, target, count in zip(paths, before, targets, [3, 1]):
            self.assertEqual(command("git", "-C", str(path), "rev-parse", "HEAD").strip(), target)
            self.assertEqual(
                command("git", "-C", str(path), "rev-list", "--count", f"{old}..HEAD").strip(),
                str(count),
            )
        self.assert_quiet_status()
        current_check = self.cli("check")
        self.assertEqual(
            current_check,
            section("Check") + f"\n  Manager  up to date @ {targets[0][:8]}\n"
            f"  Content  up to date @ {targets[1][:8]}\n",
        )
        current_status = self.cli("status")
        self.assertIn(f"Updates           none (checked {self.checked_time()})", current_status)
        # Optional proof output never enters the enrolled home or repository.
        if directory := os.environ.get("DOTFLEET_CHECK_EVIDENCE"):
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            for name, output in [
                ("behind-check", behind_check),
                ("behind-status", behind_status),
                ("current-check", current_check),
                ("current-status", current_status),
            ]:
                (folder / f"{name}.txt").write_text(output)

    def test_status_never_fetches_and_check_stays_a_separate_command(self):
        self.enroll()
        # A first enrollment ends with a check. Start this test from a machine never checked.
        (self.home / updates.CACHE).unlink(missing_ok=True)
        advance(self.origin, 3)
        before = self.snapshot()
        git_state = [
            (
                path,
                command("git", "-C", str(path), "show-ref"),
                (path / ".git/FETCH_HEAD").read_bytes()
                if (path / ".git/FETCH_HEAD").exists()
                else None,
            )
            for path in self.paths()
        ]
        output = self.cli("status")
        self.assertIn("Updates           not checked yet — run: dotfleet check", output)
        self.assertFalse((self.home / updates.CACHE).exists())
        self.assertEqual(before, self.snapshot())
        for path, refs, fetched in git_state:
            self.assertEqual(command("git", "-C", str(path), "show-ref"), refs)
            self.assertEqual(
                (path / ".git/FETCH_HEAD").read_bytes()
                if (path / ".git/FETCH_HEAD").exists()
                else None,
                fetched,
            )
        rejected = self.cli("status", "--check", expected=2)
        self.assertIn("Does not accept --check", rejected)
        self.assertFalse((self.home / updates.CACHE).exists())
        self.cli("check", expected=1)
        output = self.cli("status")
        self.assertIn(
            f"Updates           manager +3 (checked {self.checked_time()}) — run: dotfleet update",
            output,
        )
        self.assertEqual(output.count("  Updates "), 1)
        self.assertNotIn("Manager  3 commits behind", output)
        self.assertEqual(before, self.snapshot())

    def test_offline_keeps_successful_cache_and_plain_status_works(self):
        self.enroll()
        self.cli("check")
        cache = self.home / updates.CACHE
        before = (cache.read_bytes(), cache.stat().st_mtime_ns, self.snapshot())
        advance(self.origin, 3)
        self.content.rename(self.base / "offline-content")
        output = self.cli("check", expected=2)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("Warning: Could not check content: remote fetch failed", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual((cache.read_bytes(), cache.stat().st_mtime_ns, self.snapshot()), before)
        self.assertIn(f"Updates           none (checked {self.checked_time()})", self.cli("status"))
        flagged = self.cli("status", "--check", expected=2)
        self.assertIn("Does not accept --check", flagged)
        self.assertEqual((cache.read_bytes(), cache.stat().st_mtime_ns, self.snapshot()), before)
        cache.unlink()
        self.cli("check", expected=2)
        self.assertFalse(cache.exists())

    def test_nondefault_upstreams_survive_manager_replacement(self):
        self.enroll()
        targets = []
        for remote, local, count in zip([self.origin, self.content], self.paths(), [3, 1]):
            command("git", "-C", str(remote), "checkout", "-b", "stable")
            targets.append(advance(remote, count))
            command("git", "-C", str(remote), "checkout", "main")
            advance(remote, 2, content="wrong branch")
            command("git", "-C", str(local), "remote", "rename", "origin", "upstream")
            command("git", "-C", str(local), "config", "branch.main.merge", "refs/heads/stable")
        output = self.cli("check", expected=1)
        self.assertIn("Manager  3 commits behind", output)
        self.assertIn("Content  1 commit behind", output)
        self.cli("update")
        for path, target in zip(self.paths(), targets):
            self.assertEqual(command("git", "-C", str(path), "rev-parse", "HEAD").strip(), target)
            self.assertEqual(
                command("git", "-C", str(path), "config", "branch.main.remote").strip(), "upstream"
            )
            self.assertEqual(
                command("git", "-C", str(path), "config", "branch.main.merge").strip(),
                "refs/heads/stable",
            )
            self.assertEqual((path / "README.fixture").read_text().split()[0], "change")
        self.cli("check")
        self.assert_quiet_status()

    def test_git_url_rewrites_keep_the_selected_repository(self):
        self.enroll()
        self.env["GIT_CONFIG_COUNT"] = "3"
        urls = ["https://fixture.invalid/manager", "https://fixture.invalid/content"]
        targets = []
        for number, (remote, local, url) in enumerate(
            zip([self.origin, self.content], self.paths(), urls), 1
        ):
            self.env[f"GIT_CONFIG_KEY_{number}"] = f"url.{remote}.insteadOf"
            self.env[f"GIT_CONFIG_VALUE_{number}"] = url
            command("git", "-C", str(local), "remote", "set-url", "origin", url)
            targets.append(advance(remote, 1))
        profile_path = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(profile_path.read_text())
        profile["content_repo"] = urls[1]
        profile_path.write_text(toml(profile))
        self.cli("check", expected=1)
        self.cli("update")
        self.cli("check")
        for local, url, target in zip(self.paths(), urls, targets):
            self.assertEqual(
                command("git", "-C", str(local), "config", "remote.origin.url").strip(), url
            )
            self.assertEqual(command("git", "-C", str(local), "rev-parse", "HEAD").strip(), target)
        self.assert_quiet_status()

    def test_ahead_is_preserved_but_divergence_stops_check_and_update(self):
        self.enroll()
        local = self.home / MANAGER
        target = advance(local, 1, content="local")
        self.assertIn("Manager  up to date", self.cli("check"))
        self.cli("update")
        self.assertEqual(command("git", "-C", str(local), "rev-parse", "HEAD").strip(), target)
        cache = (self.home / updates.CACHE).read_bytes()
        before = self.snapshot()
        advance(self.origin, 1, content="remote")
        self.assertIn("diverged", self.cli("check", expected=2))
        self.assertEqual((self.home / updates.CACHE).read_bytes(), cache)
        self.assertIn("diverged", self.cli("update", expected=2))
        self.assertEqual(before, self.snapshot())

    def test_dirty_or_untracked_upstream_refuses_both_actions(self):
        self.enroll()
        self.cli("check")
        cache = (self.home / updates.CACHE).read_bytes()
        before = self.snapshot()
        local = self.home / MANAGER
        dirty = local / "README.fixture"
        dirty.write_text("uncommitted")
        self.assertIn("local edits", self.cli("check", expected=2))
        self.assertIn("local edits", self.cli("update", expected=2))
        dirty.unlink()
        command("git", "-C", str(local), "config", "--unset", "branch.main.merge")
        self.assertIn("no remote upstream", self.cli("check", expected=2))
        self.assertIn("no remote upstream", self.cli("update", expected=2))
        self.assertEqual((self.home / updates.CACHE).read_bytes(), cache)
        self.assertEqual(before, self.snapshot())

    def test_local_adopted_content_without_remote_matches_update(self):
        self.enroll()
        content = dotfiles_library.active(self.home)
        command("git", "-C", str(content), "remote", "remove", "origin")
        path = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(path.read_text())
        profile.update(content_repo=str(content), adopted=True, source_door="adopt")
        path.write_text(toml(profile))
        output = self.cli("check")
        self.assertIn("Content  up to date (local content; no remote)", output)
        old = command("git", "-C", str(content), "rev-parse", "HEAD")
        self.cli("update")
        self.cli("check")
        self.assertEqual(command("git", "-C", str(content), "rev-parse", "HEAD"), old)
        self.assertEqual(command("git", "-C", str(content), "remote"), "")
        self.assert_quiet_status()

    def test_short_fetch_timeout_leaves_cache_untouched(self):
        self.enroll()
        self.cli("check")
        cache = self.home / updates.CACHE
        before = (cache.read_bytes(), cache.stat().st_mtime_ns)
        local = self.home / MANAGER
        command(
            "git", "-C", str(local), "remote", "set-url", "origin", "ssh://fixture.invalid/repo"
        )
        sleeper = self.base / "slow-ssh"
        sleeper.write_text("#!/bin/sh\nsleep 30\n")
        sleeper.chmod(0o700)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        output = io.StringIO()
        started = time.monotonic()
        with (
            patch.dict(
                os.environ, dict(self.env, GIT_SSH_COMMAND=str(sleeper), GIT_SSH_VARIANT="ssh")
            ),
            patch.object(updates, "FETCH_TIMEOUT", 0.1),
            contextlib.redirect_stderr(output),
        ):
            result = updates.check(self.home, profile)
        self.assertEqual(result, 2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn("Warning: Could not check manager: remote fetch timed out", output.getvalue())
        self.assertEqual((cache.read_bytes(), cache.stat().st_mtime_ns), before)

    def test_check_help_omits_codes_and_missing_profile_warning(self):
        from test_manager import ROOT

        output = command("bash", str(ROOT / "scripts/dotfleet.sh"), "check", "--help", env=self.env)
        for text in ["upstream", "10-second"]:
            self.assertIn(text, " ".join(output.split()))
        # Help never shows exit codes; the check below still returns 2.
        self.assertNotIn("exit code", output.lower())
        self.assertNotIn("Updates are available.", output)
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dotfleet.sh"), "check"],
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Warning: Could not check updates: No profile found", result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertFalse((self.home / updates.CACHE).exists())


def usage():
    from style import ArgumentParser

    ArgumentParser(description=__doc__).parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        usage()
    else:
        unittest.main()
