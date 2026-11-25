"""Check fleet dotfiles merge-base: bring the base repository's new commits into your own.

Upstream is the base repository; origin is your own, as fleet dotfiles own leaves them.
Every remote is a local test folder. No test uses the network or GitHub.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_dotfiles_merge_base.py --help.
"""

import unittest

import test_capture
import test_command
from test_manager import command

PROMPT = b"Confirm [cancel]:"
IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]


class MergeBaseTests(unittest.TestCase):
    setUp = test_capture.CaptureTests.setUp
    terminal = test_capture.CaptureTests.terminal
    content_repository = test_capture.CaptureTests.content_repository
    manager_origin = test_capture.CaptureTests.manager_origin
    enroll = test_capture.CaptureTests.enroll
    git = test_capture.CaptureTests.git

    def own_setup(self):
        """Enroll from your own repository, with the base repository as upstream."""
        self.content_repository()
        self.mine = self.base / "mine.git"
        command("git", "clone", "-q", "--bare", str(self.bare), str(self.mine))
        self.enroll(self.mine)
        self.git(self.checkout(), "remote", "add", "upstream", str(self.bare))

    def checkout(self):
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        return checkout

    def change(self, remote, name, text, folder):
        """Commit TEXT to NAME in REMOTE, through a clone in FOLDER."""
        clone = self.base / folder
        if not clone.exists():
            command("git", "clone", "-q", str(remote), str(clone))
        (clone / name).write_text(text)
        self.git(clone, "add", "-A")
        self.git(clone, *IDENTITY, "commit", "-qm", f"change {name}")
        self.git(clone, "push", "-q", "origin", "HEAD:main")

    def head(self):
        return self.git(self.checkout(), "rev-parse", "HEAD").strip()

    def test_new_base_commits_merge_after_the_typed_word_and_reach_origin(self):
        self.own_setup()
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        output = self.terminal(["dotfiles", "merge-base"], [(PROMPT, b"merge\n")])
        self.assertIn("+export BASE_NEW=1", output)
        self.assertIn("Pushed to origin.", output)
        published = self.git(self.mine, "show", "main:.zshrc")
        self.assertIn("export BASE_NEW=1", published)
        status = self.git(self.checkout(), "status", "--porcelain").strip()
        self.assertEqual(status, "")

    def test_a_conflict_names_each_file_and_changes_nothing(self):
        self.own_setup()
        self.change(self.bare, ".file", "base version\n", "base-clone")
        self.change(self.mine, ".file", "my version\n", "mine-clone")
        self.git(self.checkout(), "pull", "-q", "origin", "main")
        before = self.head()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertIn("These files conflict with upstream: .file.", output)
        self.assertIn("Nothing changed.", output)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.git(self.checkout(), "status", "--porcelain").strip(), "")

    def test_enter_alone_cancels_and_changes_nothing(self):
        self.own_setup()
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        before = self.head()
        output = self.terminal(["dotfiles", "merge-base"], [(PROMPT, b"\n")])
        self.assertIn("Cancelled. Nothing changed.", output)
        self.assertEqual(self.head(), before)
        self.assertNotIn("BASE_NEW", self.git(self.mine, "show", "main:.zshrc"))

    def test_nothing_new_upstream_says_so(self):
        self.own_setup()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base")
        self.assertIn("Nothing new in upstream.", output)

    def test_content_without_an_upstream_says_how_to_get_one(self):
        self.content_repository()
        self.enroll(self.bare)
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertIn("This content has no upstream.", output)
        self.assertIn("dotfiles own", output)

    def test_an_update_never_merges_upstream(self):
        self.own_setup()
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        self.git(self.checkout(), "fetch", "-q", "upstream")
        test_command.CommandTests.cli(self, "update")
        self.assertNotIn("BASE_NEW", (self.home / ".zshrc").read_text())
        merged = self.git(self.checkout(), "branch", "--contains", "upstream/main").strip()
        self.assertEqual(merged, "")


if __name__ == "__main__":
    unittest.main()
