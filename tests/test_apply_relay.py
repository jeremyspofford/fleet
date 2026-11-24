"""Check that apply.sh passes every terminal line to chezmoi and records only conflict keys.

Each test runs apply.sh in a pseudo-terminal with a fake chezmoi and a hard time limit.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_apply_relay.py --help.
"""

import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 20
MENU = "(diff/overwrite/all-overwrite/skip/Skip-conflicts/quit)? "


class ApplyRelayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.temp = self.base / "tmp"
        self.temp.mkdir()
        self.out = self.base / "received"
        self.choices = self.base / "choices.txt"

    def fake_chezmoi(self, body):
        """Install a fake chezmoi that runs BODY for its apply."""
        path = self.bin / "chezmoi"
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    def run_apply(self, steps):
        """Run apply.sh in a terminal. STEPS pairs expected text with bytes to type.

        Return the exit code and the output. The test fails if apply.sh outlives LIMIT seconds.
        """
        master, slave = pty.openpty()

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            # A caller started in the background ignores Ctrl-C. A user's terminal does not.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.base),
            "TMPDIR": str(self.temp),
            "OUT": str(self.out),
            "FLEET_APPLY_CHOICES": str(self.choices),
        }
        process = subprocess.Popen(
            ["bash", str(ROOT / "scripts/apply.sh")],
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=controlling_terminal,
        )
        os.close(slave)
        transcript, cursor, answered = b"", 0, 0
        deadline = time.monotonic() + LIMIT
        try:
            while process.poll() is None and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        transcript += os.read(master, 4096)
                    except OSError:
                        break
                if answered < len(steps) and steps[answered][0] in transcript[cursor:]:
                    os.write(master, steps[answered][1])
                    cursor, answered = len(transcript), answered + 1
            # The terminal closes just before the process ends, so wait out the remaining time.
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                self.fail(f"apply.sh did not finish within {LIMIT} seconds:\n{transcript!r}")
        finally:
            os.close(master)
        return process.returncode, transcript.decode(errors="replace")

    def test_a_content_script_that_reads_input_gets_the_line_and_nothing_is_recorded(self):
        # Review F-063, items 1 and 4: an unknown prompt must not hang, and free text stays out.
        self.fake_chezmoi(
            "printf 'Type a word: '\nIFS= read -r word\nprintf '%s\\n' \"$word\" >\"$OUT\"\n"
        )
        code, output = self.run_apply([(b"Type a word: ", b"Secret123\n")])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.out.read_text(), "Secret123\n")
        self.assertFalse(self.choices.exists() and self.choices.read_text().strip())

    def test_a_conflict_key_is_recorded_trimmed_with_its_prompt(self):
        # Review F-063, item 5: chezmoi accepts " S " as skip-all, so the record must too.
        self.fake_chezmoi(
            f"printf '.zshrc has changed since chezmoi last wrote it {MENU}'\n"
            'IFS= read -r reply\nprintf \'%s\\n\' "$reply" >"$OUT"\n'
        )
        code, output = self.run_apply([(b"Choose [", b" S \n")])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.out.read_text(), " S \n")
        self.assertEqual(
            self.choices.read_text(), "S\t.zshrc has changed since chezmoi last wrote it\n"
        )

    def test_no_temporary_folder_stays_after_a_run_or_a_ctrl_c(self):
        # Review F-063, item 6.
        self.fake_chezmoi("exit 0\n")
        code, output = self.run_apply([])
        self.assertEqual(code, 0, output)
        self.assertEqual(list(self.temp.iterdir()), [])
        self.fake_chezmoi("printf 'waiting\\n'\nsleep 30\n")
        code, output = self.run_apply([(b"waiting", b"\x03")])
        self.assertNotEqual(code, 0, output)
        self.assertEqual(list(self.temp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
