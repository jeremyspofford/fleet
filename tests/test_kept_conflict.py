"""Check that a kept local edit ends the run FAILED with the command to take Fleet's version.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_kept_conflict.py --help.
"""

import json
import subprocess
import unittest

import test_command


class KeptConflictTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    terminal = test_command.CommandTests.terminal

    def test_kept_edit_names_the_file_and_the_command(self):
        self.enroll()
        path = self.home / ".file"
        path.write_text("my local edit\n")
        output = self.terminal(
            ["update"],
            [
                (b"Type one letter, then press Enter.", b"s\n"),
            ],
            expected=1,
        )
        self.assertIn("has changed since chezmoi last wrote it", output)
        self.assertIn("Managed files still differ", output)
        self.assertIn("~/.file", output)
        self.assertIn("chezmoi apply --force ~/.file", output)
        self.assertEqual(path.read_text(), "my local edit\n")
        runs = sorted((self.home / ".fleet/runs").glob("*.json"))
        self.assertEqual(json.loads(runs[-1].read_text())["result"], "FAILED")
        subprocess.run(
            ["chezmoi", "apply", "--force", str(path)], env=self.env, check=True, cwd=self.home
        )
        self.assertEqual(path.read_text(), "original\n")
        status = subprocess.run(
            ["chezmoi", "status"], env=self.env, cwd=self.home, text=True, capture_output=True
        )
        self.assertEqual(status.stdout, "")


if __name__ == "__main__":
    unittest.main()
