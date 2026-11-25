"""Check fleet dotfiles merge-base: bring the base repository's new commits into your own.

Upstream is the base repository; origin is your own, as fleet dotfiles own leaves them.
Every remote is a local test folder. No test uses the network or GitHub.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_dotfiles_merge_base.py --help.
"""

import shutil
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
        # Review F-101, blocker 3: the merge reaches this machine's home, as capture does.
        self.assertIn("export BASE_NEW=1", (self.home / ".zshrc").read_text())
        self.assertNotIn("to apply it", output)

    def fake_git(self, version, merge_tree_exit=None):
        """Put a git first on PATH that reports VERSION, and can make merge-tree fail."""
        fake = self.base / "fake-git"
        fake.mkdir(exist_ok=True)
        failing = ""
        if merge_tree_exit is not None:
            failing = f'  merge-tree) echo "usage: git merge-tree" >&2; exit {merge_tree_exit};;\n'
        # Fleet runs git -C CHECKOUT ..., so the subcommand is not the first argument.
        (fake / "git").write_text(
            "#!/bin/sh\n"
            'for argument; do case "$argument" in\n'
            f'  --version) echo "git version {version}"; exit 0;;\n'
            f"{failing}"
            "esac; done\n"
            f'exec {shutil.which("git")} "$@"\n'
        )
        (fake / "git").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"

    def assert_unchanged(self, before, output):
        self.assertIn("Nothing changed.", output)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.git(self.checkout(), "status", "--porcelain").strip(), "")
        self.assertNotIn("BASE_NEW", self.git(self.mine, "show", "main:.zshrc"))

    def test_an_origin_with_newer_commits_stops_before_anything_changes(self):
        # Review F-101, blocker 1: another machine pushed to origin first.
        self.own_setup()
        self.change(self.mine, ".other", "from another machine\n", "mine-clone")
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        before = self.head()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertIn("origin has commits that this checkout does not have.", output)
        self.assert_unchanged(before, output)

    def test_a_refused_push_puts_the_checkout_back_and_fails(self):
        # Review F-101, blocker 2: origin refuses the push.
        self.own_setup()
        hook = self.mine / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho refused by test >&2\nexit 1\n")
        hook.chmod(0o755)
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        before = self.head()
        output = self.terminal(["dotfiles", "merge-base"], [(PROMPT, b"merge\n")], expected=1)
        self.assertIn("origin did not take the merge.", output)
        self.assert_unchanged(before, output)
        self.assertNotIn("BASE_NEW", (self.home / ".zshrc").read_text())

    def test_an_ignored_local_file_that_upstream_adds_stops_it(self):
        # Review F-101, blocker 4: an ignored local file must never be overwritten.
        self.own_setup()
        self.change(self.mine, ".gitignore", "notes.local\n", "mine-clone")
        self.git(self.checkout(), "pull", "-q", "origin", "main")
        notes = self.checkout() / "notes.local"
        notes.write_text("my private notes\n")
        self.change(self.bare, "notes.local", "base file\n", "base-clone")
        before = self.head()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertIn("Upstream would overwrite these ignored local files: notes.local.", output)
        self.assert_unchanged(before, output)
        self.assertEqual(notes.read_text(), "my private notes\n")

    def test_git_older_than_2_38_stops_with_a_clear_message(self):
        self.own_setup()
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        self.fake_git("2.37.1")
        before = self.head()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertIn("needs Git 2.38 or later.\nThis machine has Git 2.37.1.", output)
        self.assert_unchanged(before, output)

    def test_a_merge_tree_error_is_not_reported_as_a_conflict(self):
        self.own_setup()
        self.change(self.bare, ".zshrc", test_capture.ZSHRC + "export BASE_NEW=1\n", "base-clone")
        self.fake_git("2.43.0", merge_tree_exit=129)
        before = self.head()
        output = test_command.CommandTests.cli(self, "dotfiles", "merge-base", expected=1)
        self.assertNotIn("conflict", output)
        self.assertIn("Git could not test the merge.", output)
        self.assert_unchanged(before, output)

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
