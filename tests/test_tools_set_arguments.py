"""Check that fleet tools set rejects a bad version before any run starts.

Usage: python3 -m unittest discover -s tests -v. No network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_tools_set_arguments.py --help.
"""

import unittest

import test_command as commands


class ToolsSetArgumentTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli

    def test_bad_version_prints_message_and_records_no_run(self):
        self.enroll()
        runs = self.home / ".fleet/runs"
        profile = self.home / ".fleet/profile.toml"
        before = sorted(path.name for path in runs.iterdir()) if runs.exists() else []
        profile_bytes = profile.read_bytes()
        for version in ("latest", "24.14"):
            with self.subTest(version=version):
                output = self.cli("tools", "set", "herdr", "--version", version, expected=2)
                self.assertIn("an exact version, such as 24.14.1", output)
                after = sorted(path.name for path in runs.iterdir()) if runs.exists() else []
                self.assertEqual(after, before)
                self.assertEqual(profile.read_bytes(), profile_bytes)


if __name__ == "__main__":
    unittest.main()
