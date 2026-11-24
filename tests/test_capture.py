"""Check fleet capture: copy a whole edited home file back into plain or Stow content.

Every remote is a local test folder. No test touches the real home or a real remote.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_capture.py --help.
"""

import shutil
import unittest

import test_command
from test_manager import ROOT, command, repository

ZSHRC = "export BASE=1\n# dotfleet:group extra begin\nexport EXTRA=1\n# dotfleet:group extra end\n"
MANIFEST = (
    'format="plain"\n'
    "[groups.extra]\n"
    'description="Extra shell lines"\n'
    'blocks=[".zshrc"]\n'
    "[domains]\n"
    'core="Base only"\n'
)
PROMPT = b"Press Enter alone to cancel."


class CaptureTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    terminal = test_command.CommandTests.terminal

    def content_repository(self):
        """Create plain content with a group block and one Stow package."""
        self.content = repository(
            self.base / "content",
            {
                "dotfleet.toml": MANIFEST,
                ".file": "original\n",
                ".zshrc": ZSHRC,
                "packages/shell/.bashrc": "export SHELL_LINE=1\n",
            },
        )
        self.bare = self.base / "content.git"
        command("git", "clone", "-q", "--bare", str(self.content), str(self.bare))

    def manager_origin(self):
        """Copy this repository's tracked files into a local manager origin, once."""
        self.origin = self.base / "manager-origin"
        if self.origin.exists():
            return
        command("git", "clone", "-q", "--no-hardlinks", str(ROOT), str(self.origin))
        command("git", "-C", str(self.origin), "checkout", "-q", "-B", "main")
        for name in command("git", "-C", str(ROOT), "ls-files", "-z").split("\0"):
            if name and (ROOT / name).is_file():
                target = self.origin / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
        command("git", "-C", str(self.origin), "add", ".")
        command(
            "git",
            "-C",
            str(self.origin),
            *identity,
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        )
        command("git", "-C", str(self.origin), "remote", "set-url", "origin", str(self.origin))
        command("git", "-C", str(self.origin), "config", "branch.main.remote", "origin")
        command("git", "-C", str(self.origin), "config", "branch.main.merge", "refs/heads/main")

    def enroll(self, remote):
        """Enroll this test's home from REMOTE, with the manager from this repository."""
        self.manager_origin()
        command(
            "bash",
            str(self.origin / "install.sh"),
            "--repo",
            str(remote),
            "--defaults",
            "--domain",
            "core",
            "--git-name",
            "Capture Example",
            "--git-email",
            "capture@example.invalid",
            env=self.env,
        )
        self.exe = self.home / ".local/bin/dotfleet"
        if str(self.home / ".local/bin") not in self.env["PATH"]:
            self.env["PATH"] = str(self.home / ".local/bin") + ":" + self.env["PATH"]

    def git(self, repository, *arguments):
        return command("git", "-C", str(repository), *arguments, env=self.env)

    def published(self, name):
        return self.git(self.bare, "show", f"main:{name}")

    def capture(self, *names, answer=b"capture\n", expected=0):
        return self.terminal(["capture", *names], [(PROMPT, answer)], expected=expected)

    def test_a_capture_pushes_and_reaches_a_second_home(self):
        self.content_repository()
        self.enroll(self.bare)
        (self.home / ".file").write_text("original\ncaptured line\n")
        (self.home / ".bashrc").write_text("export SHELL_LINE=1\nexport STOW_EDIT=1\n")
        output = self.capture(".file", ".bashrc")
        self.assertIn("Pushed to origin.", output)
        self.assertEqual(self.published(".file"), "original\ncaptured line\n")
        self.assertEqual(
            self.published("packages/shell/.bashrc"), "export SHELL_LINE=1\nexport STOW_EDIT=1\n"
        )
        subject = self.git(self.bare, "log", "-1", "--format=%s", "main").strip()
        self.assertTrue(subject.startswith("capture: .file, .bashrc from "), subject)
        # The home file now equals its rendered file, so no drift remains.
        self.assertEqual(command("chezmoi", "status", env=self.env, cwd=self.home).strip(), "")
        # A second machine receives the capture at its next update.
        first = (self.home, dict(self.env))
        second = self.base / "second"
        second.mkdir()
        self.env = {
            key: value.replace(str(self.home), str(second)) for key, value in self.env.items()
        }
        self.env["FLEET_MACHINE_NAME"] = "second-machine"
        self.home = second
        self.enroll(self.bare)
        self.assertEqual((second / ".file").read_text(), "original\ncaptured line\n")
        self.home, self.env = first
        self.assertEqual((self.home / ".file").read_text(), "original\ncaptured line\n")

    def test_a_read_only_remote_keeps_the_commit_local(self):
        self.content_repository()
        # A non-bare repository refuses a push to its checked-out branch.
        self.enroll(self.content)
        before = self.git(self.content, "rev-parse", "main").strip()
        (self.home / ".file").write_text("original\nlocal only\n")
        output = self.capture(".file")
        self.assertIn("origin is not a repository you can push to.", output)
        self.assertIn("The commit stays in ", output)
        self.assertEqual(self.git(self.content, "rev-parse", "main").strip(), before)
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        self.assertEqual(self.git(checkout, "show", "HEAD:.file"), "original\nlocal only\n")

    def test_a_disabled_groups_block_survives_a_capture(self):
        self.content_repository()
        self.enroll(self.bare)
        test_command.CommandTests.cli(self, "groups", "set", "--name", "extra", "--state", "off")
        self.assertNotIn("EXTRA", (self.home / ".zshrc").read_text())
        (self.home / ".zshrc").write_text("export BASE=1\nexport ADDED=1\n")
        self.capture(".zshrc")
        self.assertEqual(
            self.published(".zshrc"),
            "export BASE=1\nexport ADDED=1\n" + ZSHRC.removeprefix("export BASE=1\n"),
        )

    def test_a_secret_or_a_group_marker_change_is_refused(self):
        self.content_repository()
        self.enroll(self.bare)
        before = self.git(self.bare, "rev-parse", "main").strip()
        (self.home / ".file").write_text("original\nexport API_TOKEN=abcd1234efgh\n")
        output = test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertIn("~/.file line 2 looks like a secret", output)
        self.assertNotIn("abcd1234efgh", output)
        (self.home / ".file").write_text("original\n")
        (self.home / ".zshrc").write_text(ZSHRC.replace("extra begin", "other begin"))
        output = test_command.CommandTests.cli(self, "capture", ".zshrc", expected=1)
        self.assertIn("~/.zshrc changes a group marker line", output)
        self.assertEqual(self.git(self.bare, "rev-parse", "main").strip(), before)

    def test_a_conflict_with_newer_content_stops_the_file(self):
        self.content_repository()
        self.enroll(self.bare)
        # Another machine changed the same line in content.
        other = self.base / "other"
        command("git", "clone", "-q", str(self.bare), str(other))
        (other / ".file").write_text("changed elsewhere\n")
        self.git(
            other, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qam", "x"
        )
        self.git(other, "push", "-q", "origin", "HEAD:main")
        before = self.git(self.bare, "rev-parse", "main").strip()
        (self.home / ".file").write_text("changed here\n")
        output = test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertIn("~/.file conflicts with newer content", output)
        self.assertEqual(self.git(self.bare, "rev-parse", "main").strip(), before)
        self.assertEqual((self.home / ".file").read_text(), "changed here\n")

    def test_enter_alone_cancels_and_changes_nothing(self):
        self.content_repository()
        self.enroll(self.bare)
        before = self.git(self.bare, "rev-parse", "main").strip()
        (self.home / ".file").write_text("original\nnot yet\n")
        output = self.capture(".file", answer=b"\n")
        review = (ROOT / "tests/fixtures/views/capture review.txt").read_text()
        self.assertIn(review, output.replace("\r\n", "\n"))
        self.assertIn("Cancelled. Nothing changed.", output)
        self.assertEqual(self.git(self.bare, "rev-parse", "main").strip(), before)
        self.assertEqual((self.home / ".file").read_text(), "original\nnot yet\n")


if __name__ == "__main__":
    unittest.main()
