"""Check fleet capture: copy a whole edited home file back into plain or Stow content.

Every remote is a local test folder. No test touches the real home or a real remote.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_capture.py --help.
"""

import fcntl
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import unittest

import test_command
from test_manager import ROOT, command, repository

ZSHRC = "export BASE=1\n# dotfleet:group extra begin\nexport EXTRA=1\n# dotfleet:group extra end\n"
MANIFEST = (
    'format="plain"\n'
    "[groups.extra]\n"
    'description="Extra shell lines"\n'
    'blocks=[".zshrc", ".profile"]\n'
    "[domains]\n"
    'core="Base only"\n'
)
PROFILE = "A\n# dotfleet:group extra begin\nX\n# dotfleet:group extra end\nX\n"
PROMPT = b"Confirm [cancel]:"


class CaptureTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp

    def terminal(self, args, answers, expected=0, before_answer=None):
        """Run fleet ARGS in a pty and type ANSWERS. Call BEFORE_ANSWER before the first answer.

        EXPECTED None skips the exit check. The run fails after 30 seconds.
        """
        master, slave = pty.openpty()

        def setup():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            [str(self.exe), *args],
            env=self.env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            preexec_fn=setup,
        )
        data, cursor, sent = b"", 0, 0
        deadline = time.monotonic() + 30
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    data += os.read(master, 65536)
                plain = re.sub(rb"\x1b\[[0-9;]*m", b"", data[cursor:])
                if sent < len(answers) and answers[sent][0] in plain:
                    if before_answer and sent == 0:
                        before_answer()
                    os.write(master, answers[sent][1])
                    sent, cursor = sent + 1, len(data)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            while select.select([master], [], [], 0.05)[0]:
                data += os.read(master, 65536)
        finally:
            os.close(master)
            os.close(slave)
        text = re.sub(r"\x1b\[[0-9;]*m", "", data.decode(errors="replace")).replace("\r\n", "\n")
        if expected is not None:
            self.assertEqual(process.returncode, expected, text)
        self.assertEqual(sent, len(answers), text)
        return text

    def content_repository(self):
        """Create plain content with a group block and one Stow package."""
        self.content = repository(
            self.base / "content",
            {
                "dotfleet.toml": MANIFEST,
                ".file": "original\n",
                ".zshrc": ZSHRC,
                ".profile": PROFILE,
                ".tokenrc": "plain=1\n",
                ".prompt": "PS1='a\rb'\nx=1\n",
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

    def home_env(self, home, name):
        """Return this test's environment for another home, named NAME."""
        env = {key: value.replace(str(self.home), str(home)) for key, value in self.env.items()}
        env["FLEET_MACHINE_NAME"] = name
        return env

    def git(self, repository, *arguments):
        return command("git", "-C", str(repository), *arguments, env=self.env)

    def published(self, name):
        return self.git(self.bare, "show", f"main:{name}")

    def capture(self, *names, answer=b"capture\n", expected=0):
        return self.terminal(["capture", *names], [(PROMPT, answer)], expected=expected)

    def test_a_capture_pushes_and_reaches_a_second_home(self):
        self.content_repository()
        first = (self.home, dict(self.env))
        second = self.base / "second"
        second.mkdir()
        self.home, self.env = second, self.home_env(second, "second-machine")
        self.enroll(self.bare)
        self.home, self.env = first
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
        # A second machine, enrolled before the capture, receives it at its next update.
        self.home, self.env = second, self.home_env(second, "second-machine")
        self.assertEqual((second / ".file").read_text(), "original\n")
        test_command.CommandTests.cli(self, "update")
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
        output = self.terminal(["capture", ".file"], [(PROMPT, b"nope\n"), (PROMPT, b"\n")])
        self.assertIn("Choose capture, cancel. Press Enter to submit.", output)
        review = (ROOT / "tests/fixtures/views/capture review.txt").read_text()
        self.assertIn(review, output.replace("\r\n", "\n"))
        self.assertIn("Cancelled. Nothing changed.", output)
        self.assertEqual(self.git(self.bare, "rev-parse", "main").strip(), before)
        self.assertEqual((self.home / ".file").read_text(), "original\nnot yet\n")


class CaptureFollowUpTests(unittest.TestCase):
    """Review F-078: one test per defect."""

    setUp = CaptureTests.setUp
    terminal = CaptureTests.terminal
    content_repository = CaptureTests.content_repository
    manager_origin = CaptureTests.manager_origin
    enroll = CaptureTests.enroll
    home_env = CaptureTests.home_env
    git = CaptureTests.git
    published = CaptureTests.published
    capture = CaptureTests.capture

    def checkout(self):
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        return checkout

    def other_clone(self):
        other = self.base / "other"
        if not other.exists():
            command("git", "clone", "-q", str(self.bare), str(other))
        return other

    def push_from_other(self, change):
        other = self.other_clone()
        change(other)
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        self.git(other, "add", "-A")
        self.git(other, *identity, "commit", "-qm", "elsewhere")
        self.git(other, "push", "-q", "origin", "HEAD:main")

    def test_a_bare_carriage_return_survives_a_capture(self):
        self.content_repository()
        self.enroll(self.bare)
        (self.home / ".prompt").write_bytes(b"PS1='a\rb'\nx=1\ny=2\n")
        self.capture(".prompt")
        # Read bytes: text mode would turn the carriage return into a newline.
        published = subprocess.run(
            ["git", "-C", str(self.bare), "cat-file", "-p", "main:.prompt"],
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(published, b"PS1='a\rb'\nx=1\ny=2\n")

    def test_newer_content_that_removes_the_file_refuses_it_by_name(self):
        self.content_repository()
        self.enroll(self.bare)
        self.push_from_other(lambda other: (other / ".file").unlink())
        (self.home / ".file").write_text("original\nmine\n")
        output = test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertIn("~/.file was removed or renamed in newer content.", output)
        self.assertNotIn("Traceback", output)

    def test_the_users_ssh_command_is_kept_with_batch_mode(self):
        self.content_repository()
        self.enroll(self.bare)
        record = self.base / "ssh-arguments"
        fake = self.base / "fake-ssh"
        fake.mkdir()
        (fake / "myssh").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >>"{record}"\nexit 255\n')
        (fake / "myssh").chmod(0o755)
        checkout = self.checkout()
        self.git(checkout, "remote", "set-url", "origin", "ssh://git@example.invalid/c.git")
        self.git(checkout, "config", "core.sshCommand", f"{fake}/myssh -o IdentitiesOnly=yes")
        (self.home / ".file").write_text("original\nkeyed\n")
        self.capture(".file", expected=1)
        arguments = record.read_text()
        self.assertIn("IdentitiesOnly=yes", arguments)
        self.assertIn("BatchMode=yes", arguments)

    def test_a_refused_push_after_a_good_fetch_means_not_your_origin(self):
        self.content_repository()
        self.enroll(self.bare)
        hook = self.bare / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho 'Permission denied (publickey).' >&2\nexit 1\n")
        hook.chmod(0o755)
        (self.home / ".file").write_text("original\nrefused push\n")
        output = self.capture(".file")
        self.assertIn("origin is not a repository you can push to.", output)
        self.assertNotIn("could not be reached", output)

    def test_content_without_a_remote_is_not_yours_to_push(self):
        self.content_repository()
        self.enroll(self.bare)
        self.git(self.checkout(), "remote", "remove", "origin")
        (self.home / ".file").write_text("original\nno remote\n")
        output = self.capture(".file", expected=None)
        self.assertIn("This content has no origin.", output)
        self.assertNotIn("could not be reached", output)

    def test_origin_moving_during_the_prompt_changes_nothing(self):
        self.content_repository()
        self.enroll(self.bare)
        before = self.git(self.checkout(), "rev-parse", "HEAD").strip()
        (self.home / ".file").write_text("original\nduring the prompt\n")
        self.push_from_other(lambda other: (other / ".zshrc").write_text(ZSHRC + "x=1\n"))
        moved = self.git(self.bare, "rev-parse", "main").strip()
        original_fetch = self.git(self.checkout(), "config", "remote.origin.fetch").strip()
        # The capture fetched before this change; the prompt then waits while origin moves.
        self.git(self.checkout(), "config", "remote.origin.fetch", "+refs/heads/nothing:refs/x")
        output = self.terminal(
            ["capture", ".file"],
            [(PROMPT, b"capture\n")],
            expected=1,
            before_answer=lambda: self.git(
                self.checkout(), "config", "remote.origin.fetch", original_fetch
            ),
        )
        self.assertIn("origin changed while you reviewed. Nothing changed.", output)
        self.assertEqual(self.git(self.checkout(), "rev-parse", "HEAD").strip(), before)
        self.assertEqual(self.git(self.bare, "rev-parse", "main").strip(), moved)

    def test_a_second_capture_or_a_detached_checkout_ends_cleanly(self):
        self.content_repository()
        self.enroll(self.bare)
        self.git(self.checkout(), "remote", "set-url", "origin", str(self.base / "gone.git"))
        (self.home / ".file").write_text("original\ntwice\n")
        self.capture(".file", expected=1)
        output = test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertIn("~/.file already matches content.", output)
        self.git(self.checkout(), "checkout", "-q", "--detach")
        output = test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertIn("The content checkout is not on a branch.", output)
        self.assertNotIn("Traceback", output)

    def test_the_final_newline_is_backed_up_and_only_added_after_a_push(self):
        self.content_repository()
        self.enroll(self.bare)
        test_command.CommandTests.cli(self, "groups", "set", "--name", "extra", "--state", "off")
        (self.home / ".zshrc").write_text("export BASE=1\nexport ADDED=1")
        self.capture(".zshrc")
        saved = [path.read_text() for path in (self.home / ".fleet/backups").glob("*/home/.zshrc")]
        self.assertIn("export BASE=1\nexport ADDED=1", saved)
        # With origin unreachable, nothing is pushed, so the home file stays as typed.
        self.git(self.checkout(), "remote", "set-url", "origin", str(self.base / "gone.git"))
        (self.home / ".zshrc").write_text("export BASE=1\nexport ADDED=1\nexport MORE=1")
        self.capture(".zshrc", expected=1)
        self.assertEqual(
            (self.home / ".zshrc").read_text(), "export BASE=1\nexport ADDED=1\nexport MORE=1"
        )


class AskFileTests(unittest.TestCase):
    """The terminal interface asks for the file in the terminal (review F-074, Standards)."""

    def ask(self, typed):
        master, slave = pty.openpty()

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            [sys.executable, "-c", "import capture; print(repr(capture.ask_file()))"],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "scripts")),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=controlling_terminal,
        )
        output, deadline = b"", time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                output += os.read(master, 4096)
                if b"Press Enter alone to cancel." in output and typed:
                    os.write(master, typed)
                    typed = b""
        if process.poll() is None:
            process.kill()
            self.fail(f"ask_file() did not finish: {output!r}")
        while select.select([master], [], [], 0.05)[0]:
            output += os.read(master, 4096)
        os.close(master)
        os.close(slave)
        return output.decode()

    def test_a_typed_path_is_returned_and_enter_alone_cancels(self):
        self.assertIn("'~/.zshrc'", self.ask(b"~/.zshrc\n"))
        self.assertIn("''", self.ask(b"\n"))


class CaptureCorrectionTests(unittest.TestCase):
    """Review F-074: one test per defect."""

    setUp = CaptureTests.setUp
    terminal = CaptureTests.terminal
    content_repository = CaptureTests.content_repository
    manager_origin = CaptureTests.manager_origin
    enroll = CaptureTests.enroll
    home_env = CaptureTests.home_env
    git = CaptureTests.git
    published = CaptureTests.published
    capture = CaptureTests.capture

    def test_a_home_file_without_a_final_newline_keeps_the_marker_on_its_own_line(self):
        self.content_repository()
        self.enroll(self.bare)
        test_command.CommandTests.cli(self, "groups", "set", "--name", "extra", "--state", "off")
        (self.home / ".zshrc").write_text("export BASE=1\nexport ADDED=1")
        self.capture(".zshrc")
        published = self.published(".zshrc")
        self.assertIn("export ADDED=1\n# dotfleet:group extra begin\n", published)
        self.assertEqual((self.home / ".zshrc").read_text(), "export BASE=1\nexport ADDED=1\n")

    def test_an_edit_never_lands_inside_a_disabled_block(self):
        self.content_repository()
        self.enroll(self.bare)
        test_command.CommandTests.cli(self, "groups", "set", "--name", "extra", "--state", "off")
        self.assertEqual((self.home / ".profile").read_text(), "A\nX\n")
        (self.home / ".profile").write_text("A\nY\n")
        self.capture(".profile")
        self.assertEqual(self.published(".profile"), PROFILE.removesuffix("X\n") + "Y\n")

    def test_an_edit_that_spans_a_disabled_block_is_refused_by_name(self):
        self.content_repository()
        self.enroll(self.bare)
        test_command.CommandTests.cli(self, "groups", "set", "--name", "extra", "--state", "off")
        (self.home / ".profile").write_text("B\nY\n")
        output = test_command.CommandTests.cli(self, "capture", ".profile", expected=1)
        self.assertIn("~/.profile changes lines around the disabled group extra", output)

    def newer_content(self):
        """Push a content change to another file, as another machine would."""
        other = self.base / "other"
        command("git", "clone", "-q", str(self.bare), str(other))
        (other / ".zshrc").write_text(ZSHRC + "export NEWER=1\n")
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        self.git(other, *identity, "commit", "-qam", "newer")
        self.git(other, "push", "-q", "origin", "HEAD:main")

    def test_a_cancelled_or_refused_capture_leaves_the_checkout_where_it_was(self):
        self.content_repository()
        self.enroll(self.bare)
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        before = self.git(checkout, "rev-parse", "HEAD").strip()
        self.newer_content()
        (self.home / ".file").write_text("original\nnot yet\n")
        self.terminal(["capture", ".file"], [(PROMPT, b"\n")])
        self.assertEqual(self.git(checkout, "rev-parse", "HEAD").strip(), before)
        (self.home / ".file").write_text("original\nexport API_TOKEN=abcd1234efgh\n")
        test_command.CommandTests.cli(self, "capture", ".file", expected=1)
        self.assertEqual(self.git(checkout, "rev-parse", "HEAD").strip(), before)
        # The next update still sees the newer content.
        (self.home / ".file").write_text("original\n")
        test_command.CommandTests.cli(self, "update")
        self.assertIn("export NEWER=1", (self.home / ".zshrc").read_text())

    def test_an_unreachable_origin_is_reported_as_unreachable(self):
        self.content_repository()
        self.enroll(self.bare)
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        self.git(checkout, "remote", "set-url", "origin", str(self.base / "gone.git"))
        (self.home / ".file").write_text("original\noffline\n")
        output = self.capture(".file", expected=1)
        self.assertIn("Could not fetch content from origin.", output)
        self.assertIn("origin could not be reached. The commit stays in ", output)
        self.assertNotIn("not a repository you can push to", output)

    def test_an_ssh_origin_never_waits_for_a_password(self):
        self.content_repository()
        self.enroll(self.bare)
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        self.git(checkout, "remote", "set-url", "origin", "ssh://git@example.invalid/content.git")
        record = self.base / "ssh-arguments"
        fake = self.base / "fake-ssh"
        fake.mkdir()
        (fake / "ssh").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >>"{record}"\nexit 255\n')
        (fake / "ssh").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        (self.home / ".file").write_text("original\nover ssh\n")
        self.capture(".file", expected=1)
        self.assertIn("BatchMode=yes", record.read_text())

    def test_a_refused_file_is_left_alone_by_the_update(self):
        self.content_repository()
        self.enroll(self.bare)
        (self.home / ".file").write_text("original\nexport API_TOKEN=abcd1234efgh\n")
        (self.home / ".bashrc").write_text("export SHELL_LINE=1\nexport STOW_EDIT=1\n")
        output = self.capture(".file", ".bashrc", expected=1)
        self.assertIn("Fleet did not run the update, because a file was refused.", output)
        self.assertNotIn("abcd1234efgh", output)
        self.assertEqual(
            (self.home / ".file").read_text(), "original\nexport API_TOKEN=abcd1234efgh\n"
        )
        self.assertIn("STOW_EDIT", self.published("packages/shell/.bashrc"))

    def test_a_sensitive_file_name_is_refused(self):
        self.content_repository()
        self.enroll(self.bare)
        (self.home / ".tokenrc").write_text("plain=2\n")
        output = test_command.CommandTests.cli(self, "capture", ".tokenrc", expected=1)
        self.assertIn("~/.tokenrc has a credential file name.", output)


if __name__ == "__main__":
    unittest.main()
