"""Check that a local edit kept at the conflict prompt completes the run with one warning.

An unattended run still stops before it overwrites a local edit.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_kept_conflict.py --help.
"""

import hashlib
import json
import subprocess
import unittest

import test_command


class KeptConflictTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    terminal = test_command.CommandTests.terminal

    def test_a_kept_edit_completes_the_run_and_names_the_command(self):
        self.enroll()
        outputs = self.home / ".fleet/outputs.json"
        fleet_version = json.loads(outputs.read_text())[".file"]
        path = self.home / ".file"
        path.write_text("my local edit\n")
        output = self.terminal(
            ["update"],
            [
                (b"Type one letter, then press Enter.", b"s\n"),
            ],
        )
        self.assertIn("has changed since chezmoi last wrote it", output)
        self.assertNotIn("Setup failed", output)
        self.assertNotIn("chezmoi status: failed", output)
        self.assertIn("Kept by you: ~/.file.", output)
        self.assertIn("chezmoi apply --force ~/.file", output)
        self.assertIn("chezmoi status: 1 kept by you", output)
        self.assertEqual(path.read_text(), "my local edit\n")
        runs = sorted((self.home / ".fleet/runs").glob("*.json"))
        self.assertEqual(json.loads(runs[-1].read_text())["result"], "SUCCESS")
        # The inventory keeps Fleet's version, so a later removal still protects the edit.
        edited = hashlib.sha256(b"my local edit\n").hexdigest()
        self.assertEqual(json.loads(outputs.read_text())[".file"], fleet_version)
        self.assertNotEqual(fleet_version["sha256"], edited)
        subprocess.run(
            ["chezmoi", "apply", "--force", str(path)], env=self.env, check=True, cwd=self.home
        )
        self.assertEqual(path.read_text(), "original\n")
        status = subprocess.run(
            ["chezmoi", "status"], env=self.env, cwd=self.home, text=True, capture_output=True
        )
        self.assertEqual(status.stdout, "")

    def test_a_difference_without_a_local_edit_still_fails_the_run(self):
        # Only a file with a local edit counts as kept. A file the apply never reached does not.
        self.enroll()
        (self.home / ".file").write_text("my local edit\n")
        # A new content commit changes .zshrc, which chezmoi reaches after .file.
        (self.content / ".zshrc").write_text('source "$HOME/.aliases"\n# newer\n')
        git = ["git", "-C", str(self.content), "-c", "user.name=Test"]
        git += ["-c", "user.email=test@example.invalid"]
        subprocess.run([*git, "commit", "-qam", "feat: newer zshrc"], check=True)
        output = self.terminal(
            ["update"],
            [(b"Type one letter, then press Enter.", b"q\n")],
            expected=1,
        )
        self.assertIn("Managed files still differ after the apply: ~/.zshrc.", output)
        self.assertIn("Setup failed", output)
        self.assertEqual((self.home / ".file").read_text(), "my local edit\n")

    def test_an_unattended_run_still_stops_before_a_local_edit(self):
        self.enroll()
        path = self.home / ".file"
        path.write_text("my local edit\n")
        result = subprocess.run(
            [str(self.exe), "update"],
            env={**self.env, "FLEET_REMOTE_RUN": "1"},
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Apply stopped. Review changes in a terminal", output)
        self.assertNotIn("Kept by you", output)
        self.assertEqual(path.read_text(), "my local edit\n")
        runs = sorted((self.home / ".fleet/runs").glob("*.json"))
        self.assertEqual(json.loads(runs[-1].read_text())["result"], "FAILED")


if __name__ == "__main__":
    unittest.main()
