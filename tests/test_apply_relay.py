"""Check that apply.sh runs chezmoi on its own terminal and records only conflict answers.

Each test runs apply.sh in a pseudo-terminal under a hard time limit.
Some tests use real chezmoi in a temporary home; others use a fake chezmoi.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_apply_relay.py --help.
"""

import fcntl
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import termios
import time
import tty
import unittest

import apply_terminal
import manager

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 30
MENU = "(diff/overwrite/all-overwrite/skip/Skip-conflicts/quit)? "
TAIL = " has changed since chezmoi last wrote it"


class ApplyTerminalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.out = self.home / "received"
        self.choices = self.base / "choices.txt"

    def fake_chezmoi(self, body):
        """Install a fake chezmoi that runs BODY for its apply."""
        path = self.bin / "chezmoi"
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    def env(self):
        """Return a clean environment: this test's home, and no inherited XDG or chezmoi state."""
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "TERM": "xterm",
            "OUT": str(self.out),
            "FLEET_APPLY_CHOICES": str(self.choices),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def real_chezmoi(self, files, scripts=()):
        """Apply FILES with real chezmoi, then edit each locally, so each is a conflict."""
        location = shutil.which("chezmoi", path=os.environ.get("PATH", "")) or str(
            Path.home() / ".local/bin/chezmoi"
        )
        if not Path(location).exists():
            self.skipTest("chezmoi is not installed")
        (self.bin / "chezmoi").symlink_to(location)
        source = self.home / ".local/share/chezmoi"
        source.mkdir(parents=True)
        for name in files:
            (source / ("dot_" + name)).write_text(f"{name} from content\n")
        subprocess.run(
            ["chezmoi", "--no-tty", "apply"], env=self.env(), check=True, capture_output=True
        )
        for name in files:
            (self.home / ("." + name)).write_text(f"{name} edited here\n")
        for name, body in scripts:
            (source / name).write_text("#!/usr/bin/env bash\n" + body)

    def status(self):
        return subprocess.run(
            ["chezmoi", "status"], env=self.env(), text=True, capture_output=True
        ).stdout

    def run_apply(self, steps):
        """Run apply.sh in a terminal. Return (exit code, output).

        STEPS lists (text to wait for, bytes to type, seconds to wait before typing).
        A step's text must appear after the previous step. The test fails past LIMIT seconds.
        The terminal settings must be the same afterward.
        """
        master, slave = pty.openpty()
        before = termios.tcgetattr(master)

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        process = subprocess.Popen(
            ["bash", str(ROOT / "scripts/apply.sh")],
            env=self.env(),
            cwd=self.home,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=controlling_terminal,
        )
        transcript, cursor, step = b"", 0, 0
        deadline = time.monotonic() + LIMIT
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    transcript += os.read(master, 4096)
                if step < len(steps) and steps[step][0] in transcript[cursor:]:
                    _, typed, delay = steps[step]
                    time.sleep(delay)
                    os.write(master, typed)
                    cursor, step = len(transcript), step + 1
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                self.fail(f"apply.sh did not finish within {LIMIT} seconds:\n{transcript!r}")
            while select.select([master], [], [], 0.05)[0]:
                transcript += os.read(master, 4096)
            after = termios.tcgetattr(master)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual(after, before, "the terminal settings were not restored")
        return process.returncode, transcript.decode(errors="replace")

    def records(self):
        return self.choices.read_text() if self.choices.exists() else ""

    def test_three_conflicts_with_slow_answers_all_apply(self):
        # Review F-065 blocker: a 1-second quiet time read as the end of chezmoi's output.
        self.real_chezmoi(["a", "b", "c"])
        code, output = self.run_apply([(b"Choose [", b"s\r", 2.0)] * 3)
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count("File conflict"), 3, output)
        self.assertEqual(self.status(), "MM .a\nMM .b\nMM .c\n")
        expected = "".join(f"skip\t.{name}{TAIL}\n" for name in "abc")
        self.assertEqual(self.records(), expected)

    def test_a_terminals_answers_to_chezmoi_queries_are_not_typing(self):
        # chezmoi 2.74.1 asks a terminal for its background color and cursor position.
        # A real terminal answers with input bytes; they must not read as type-ahead.
        self.real_chezmoi(["a", "b"])
        answers = b"\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[1;1R"
        steps = [(b"\x1b[6n", answers, 0), (b"Choose [", b"S\r", 0)]
        code, output = self.run_apply(steps)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.records(), f"Skip-conflicts\t.a{TAIL}\n")
        self.assertEqual(self.status(), "MM .a\nMM .b\n")

    def test_a_key_that_looks_like_an_answer_at_the_query_is_typing(self):
        # Review F-080, B1: Ctrl+F3 (ESC[1;5R) arrives first and chezmoi takes it as its answer.
        # The real answers then sit in the line, so chezmoi rejects S and asks again.
        self.real_chezmoi(["a", "b"])
        answers = b"\x1b[1;5R\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[24;1R"
        steps = [(b"\x1b[6n", answers, 0), (b"Choose [", b"S\r", 0), (b"Choose [", b"q\r", 0)]
        code, output = self.run_apply(steps)
        self.assertEqual(code, 0, output)
        self.assertNotIn("Skip-conflicts", self.records())
        kept, not_reviewed, _ = manager.sort_differences(self.status(), self.records())
        self.assertEqual((kept, not_reviewed), ([".a"], [".b"]))

    def test_a_late_answer_after_chezmoi_stopped_waiting_is_typing(self):
        # Review F-080: chezmoi waits about 5 s. A later answer starts the next line it reads.
        self.real_chezmoi(["a", "b"], [("run_before_wait.sh", "sleep 4\n")])
        answers = b"\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[24;1R"
        steps = [(b"\x1b[6n", answers, 6.0), (b"Choose [", b"S\r", 0), (b"Choose [", b"q\r", 0)]
        code, output = self.run_apply(steps)
        self.assertEqual(code, 0, output)
        self.assertNotIn("Skip-conflicts", self.records())

    def test_a_quiet_content_script_does_not_end_the_apply(self):
        self.real_chezmoi(["a"], [("run_before_quiet.sh", "sleep 2\n")])
        code, output = self.run_apply([(b"Choose [", b"s\r", 0)])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.records(), f"skip\t.a{TAIL}\n")

    def test_a_content_script_reads_a_password_and_the_terminal_as_without_fleet(self):
        # read -s hides input, read -p shows its prompt, and /dev/tty readers get their line.
        script = (
            'read -r -s -p "Password: " password\necho\n'
            'read -r -p "Line: " line </dev/tty\n'
            'printf "%s|%s" "$password" "$line" >"$HOME/received"\n'
        )
        self.real_chezmoi([], [("run_before_ask.sh", script)])
        steps = [(b"Password: ", b"hunter2\r", 0), (b"Line: ", b"main\r", 0)]
        code, output = self.run_apply(steps)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.out.read_text(), "hunter2|main")
        self.assertNotIn("hunter2", output)
        self.assertEqual(self.records(), "")

    def test_scripts_that_did_not_run_after_quit_still_fail_the_run(self):
        # Review F-076, item 3, with real chezmoi: q stops before any script runs.
        scripts = [
            ("run_every.sh", "true\n"),
            ("run_once_new.sh", "true\n"),
            ("run_onchange_x.sh", "true\n"),
        ]
        self.real_chezmoi(["a"], scripts)
        code, output = self.run_apply([(b"Choose [", b"q\r", 0)])
        self.assertEqual(code, 0, output)
        listing = ["chezmoi", "managed", "--include", "scripts", "--path-style", "all"]
        managed = subprocess.run(
            [*listing, "--format", "json"], env=self.env(), text=True, capture_output=True
        ).stdout
        every_run = manager.every_run_scripts(managed)
        self.assertEqual(every_run, {"every.sh"})
        _, _, others = manager.sort_differences(self.status(), self.records(), every_run)
        self.assertEqual(others, ["new.sh", "x.sh"])

    def test_type_ahead_before_a_menu_is_never_recorded(self):
        # Review F-063, item 4: a line typed before the menu may be a secret.
        self.real_chezmoi(["a"], [("run_before_wait.sh", "sleep 1\n")])
        steps = [(b"Applying", b"secret\r", 0), (b"Choose [", b"s\r", 0)]
        code, output = self.run_apply(steps)
        self.assertEqual(code, 0, output)
        self.assertNotIn("secret", self.records())
        self.assertNotIn("skip\t", self.records().splitlines()[0])

    def test_a_skip_all_answer_with_spaces_is_recorded_as_the_menu_word(self):
        self.fake_chezmoi(
            f"printf '.zshrc{TAIL} {MENU}'\n"
            'IFS= read -r reply\nprintf \'%s\\n\' "$reply" >"$OUT"\n'
        )
        code, output = self.run_apply([(b"Choose [", b" Sk \r", 0)])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.out.read_text(), " Sk \n")
        self.assertEqual(self.records(), f"Skip-conflicts\t.zshrc{TAIL}\n")

    def test_ctrl_c_stops_chezmoi_and_restores_the_terminal(self):
        self.fake_chezmoi("printf 'waiting\\n'\nsleep 30\n")
        code, output = self.run_apply([(b"waiting", b"\x03", 0)])
        self.assertEqual(code, 130, output)

    def test_an_answer_with_escape_or_control_bytes_is_never_recorded_as_skip_all(self):
        # Review F-072, Spec 3: chezmoi rejects these lines and asks again; q then quits.
        # The record must not say Skip-conflicts, or .b would count as kept unasked.
        # Review F-076: Ctrl+F3 (ESC[1;5R) and a late cursor answer look like terminal answers.
        typed_lines = (
            b"\x1b[AS\r",
            b"s\x08S\r",
            b"\x1b[200~S\x1b[201~\r",
            b"\x1b[1;5RS\r",
            b"\x1b[24;80RS\r",
        )
        for typed in typed_lines:
            with self.subTest(typed=typed):
                self.setUp()
                self.real_chezmoi(["a", "b"])
                steps = [(b"Choose [", typed, 0), (b"Choose [", b"q\r", 0)]
                code, output = self.run_apply(steps)
                self.assertEqual(code, 0, output)
                self.assertNotIn("Skip-conflicts", self.records())
                kept, not_reviewed, _ = manager.sort_differences(self.status(), self.records())
                self.assertEqual((kept, not_reviewed), ([".a"], [".b"]))


class ResolveTests(unittest.TestCase):
    """chezmoi 2.74.1 accepts a choice or any prefix that only one choice has (item 5)."""

    CHOICES = ["diff", "overwrite", "all-overwrite", "skip", "Skip-conflicts", "quit"]

    def test_every_form_of_skip_all_resolves_to_skip_conflicts(self):
        for answer in ["S", "Sk", "Ski", "Skip", "Skip-", "Skip-conflicts", "  S  "]:
            with self.subTest(answer=answer):
                self.assertEqual(apply_terminal.resolve(answer, self.CHOICES), "Skip-conflicts")

    def test_skip_quit_and_invalid_answers(self):
        self.assertEqual(apply_terminal.resolve("sk", self.CHOICES), "skip")
        self.assertEqual(apply_terminal.resolve("q", self.CHOICES), "quit")
        for answer in ["", "x", "Secret123"]:
            self.assertIsNone(apply_terminal.resolve(answer, self.CHOICES))

    def test_only_an_every_run_script_line_is_not_a_file_conflict(self):
        # Review F-076: a run_once_ or run_onchange_ script that did not run still fails the run.
        status = "MM .a\n R every.sh\n R new.sh\n R x.sh\n"
        records = f"skip\t.a{TAIL}\n"
        kept, not_reviewed, others = manager.sort_differences(status, records, {"every.sh"})
        self.assertEqual((kept, not_reviewed, others), ([".a"], [], ["new.sh", "x.sh"]))

    def test_unexpected_script_listings_fail_cleanly(self):
        # Review F-080, follow-up 3: odd JSON stops the run with a clear error, never a crash.
        for listing in ("[]", "null", "{not json"):
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                manager.every_run_scripts(listing)

    def test_a_recorded_skip_all_keeps_every_file_and_a_question_mark_does_not(self):
        status = "MM .a\nMM .b\n"
        kept, not_reviewed, _ = manager.sort_differences(status, f"Skip-conflicts\t.a{TAIL}\n")
        self.assertEqual((kept, not_reviewed), ([".a", ".b"], []))
        kept, not_reviewed, _ = manager.sort_differences(status, f"?\t.a{TAIL}\n")
        self.assertEqual((kept, not_reviewed), ([".a"], [".b"]))


class TerminalRestoredTests(unittest.TestCase):
    """Review F-072, Spec 1: Fleet restores the terminal even when the helper is killed."""

    def test_the_settings_return_after_the_block_and_after_an_error(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = termios.tcgetattr(slave)
        with manager.terminal_restored(os.ttyname(slave)):
            tty.setraw(slave)
        self.assertEqual(termios.tcgetattr(slave), before)
        with self.assertRaises(RuntimeError):
            with manager.terminal_restored(os.ttyname(slave)):
                tty.setraw(slave)
                raise RuntimeError("the helper was killed")
        self.assertEqual(termios.tcgetattr(slave), before)

    def test_without_a_terminal_the_block_still_runs(self):
        ran = []
        with manager.terminal_restored("/nonexistent/tty"):
            ran.append(True)
        self.assertEqual(ran, [True])


class DifferenceProblemTests(unittest.TestCase):
    """Review F-072, Spec 5: after an unconfirmed answer, say so instead of blaming the apply."""

    def test_unconfirmed_and_unreached_files_get_their_own_words(self):
        text = manager.difference_problems([".b"], [], unconfirmed=True)
        self.assertIn("Unconfirmed: ~/.b. Fleet could not confirm an earlier answer.", text)
        self.assertIn("That answer may have kept these files.", text)
        self.assertNotIn("Not reviewed", text)
        text = manager.difference_problems([".b"], [".c"], unconfirmed=False)
        self.assertIn("Not reviewed: ~/.b. Fleet saw no answer for these local changes.", text)
        self.assertIn("Managed files still differ after the apply: ~/.c.", text)
        self.assertEqual(manager.difference_problems([], [], unconfirmed=False), "")


if __name__ == "__main__":
    unittest.main()
