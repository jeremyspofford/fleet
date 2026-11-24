"""Check the local registry schema, refreshes, and rendered recorded-machine view.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_registry.py --help.
"""

import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import test_command as commands
from common import atomic, toml
import registry
import run_index


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        atomic(
            self.home / ".fleet/profile.toml",
            toml(
                {
                    "domain": "personal",
                    "domain_chosen": True,
                    "content_repo": "https://example.invalid/content",
                    "git_email": "private@example.invalid",
                    "pins": {"node": "24.14.1"},
                }
            ),
        )

    def test_refresh_preserves_other_machine_and_private_profile_fields_stay_out(self):
        with patch("socket.gethostname", return_value="other-machine"):
            other = registry.refresh(
                self.home, "0.2.0", self.home / ".fleet/backups/first", "SUCCESS"
            )
        # Another machine has its own ID and its own saved name.
        # Without them, this record would be a rename of the first one.
        (self.home / registry.MACHINE_ID).unlink()
        (self.home / run_index.NAME_FILE).unlink()
        with patch("socket.gethostname", return_value="this-machine"):
            first = registry.refresh(
                self.home, "0.2.1", self.home / ".fleet/backups/second", "SUCCESS"
            )
            latest = registry.refresh(
                self.home, "0.2.1", self.home / ".fleet/backups/third", "FAILED"
            )
        data = registry.load(self.home)
        self.assertEqual(data["machines"]["other-machine"], other)
        self.assertEqual(data["machines"]["this-machine"], latest)
        self.assertNotEqual(first["last_run_record"], latest["last_run_record"])
        text = (self.home / registry.REGISTRY).read_text()
        self.assertNotIn("private@example.invalid", text)
        self.assertNotIn("24.14.1", text)
        self.assertEqual((self.home / registry.REGISTRY).stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.home / ".fleet/dotfiles").exists())

    def test_legacy_view_is_unchosen_and_does_not_create_store(self):
        atomic(
            self.home / ".fleet/profile.toml",
            toml({"domain": "core", "content_repo": "https://example.invalid/content"}),
        )
        entities = registry.entities(self.home, "0.2.0")
        self.assertEqual(len(entities), 1)
        self.assertIn(("Domain", "core · UNCHOSEN"), entities[0]["fields"])
        self.assertFalse((self.home / registry.REGISTRY).exists())

    def test_invalid_schema_and_links_fail_without_replacing_data(self):
        path = self.home / registry.REGISTRY
        atomic(path, "schema_version = 99\n[machines]\n")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "schema_version"):
            registry.refresh(self.home, "0.2.0", self.home / "backup", "SUCCESS")
        self.assertEqual(path.read_bytes(), before)
        path.unlink()
        other = self.home / "keep"
        other.write_text("keep")
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "symlink"):
            registry.load(self.home)
        self.assertEqual(other.read_text(), "keep")


class RegistryCommandTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    terminal = commands.CommandTests.terminal

    def test_enrollment_rerun_backup_and_machines_preserve_other_records(self):
        self.enroll()
        path = self.home / registry.REGISTRY
        data = registry.load(self.home)
        local = socket.gethostname()
        first = data["machines"][local]
        self.assertEqual(first["domain"], "core")
        self.assertTrue(first["domain_chosen"])
        self.assertEqual(len(first["content_commit"]), 40)
        self.assertTrue(Path(first["last_run_record"]).is_file())
        other = copy.deepcopy(first)
        other.update(
            hostname="recorded-machine",
            home="/home/recorded",
            domain="work",
            last_run="2026-11-01T10:00:00+00:00",
            last_backup="/home/recorded/.fleet/backups/20261101-100000",
            last_run_record="/home/recorded/.fleet/backups/20261101-100000/RUN.json",
            machine_id="0" * 32,
        )
        data["machines"]["recorded-machine"] = other
        atomic(path, registry.serialize(data))
        before = path.read_bytes()
        output = self.cli("machines", "list")
        self.assertEqual(path.read_bytes(), before)
        self.assertIn("this machine", output)
        self.assertIn("recorded-machine · recorded only", output)
        self.assertNotIn("Scope", output)
        self.assertNotIn("RUN.json", output)
        self.assertNotIn("{", output)
        for line in output.splitlines():
            self.assertLessEqual(len(line), 100)
        Path("/tmp/dotfleet-machines-example.txt").write_text(output)
        from test_updates import advance

        advance(self.content, 1, ".file", "new content")
        self.cli("update")
        self.assertEqual(registry.load(self.home)["machines"]["recorded-machine"], other)
        after = registry.load(self.home)["machines"][local]
        self.assertNotEqual(after["last_run_record"], first["last_run_record"])
        self.cli("backups", "create")
        latest = registry.load(self.home)["machines"][local]
        self.assertNotEqual(latest["last_run_record"], after["last_run_record"])
        self.assertEqual(
            json.loads(Path(latest["last_run_record"]).read_text())["result"], latest["result"]
        )
        self.assertEqual(self.cli("addons", "list").count("No installed add-ons"), 1)

    def test_content_cannot_replace_private_registry_or_profile(self):
        self.enroll()
        from test_manager import command

        for name in [registry.REGISTRY, ".fleet/profile.toml"]:
            target = self.content / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# must never reach home\n")
            command("git", "-C", str(self.content), "add", "--force", name)
            command(
                "git",
                "-C",
                str(self.content),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "test: forbidden content state",
            )
            before = (self.home / registry.REGISTRY).read_bytes()
            output = self.cli("update", expected=1)
            self.assertIn("Content cannot own manager state", output)
            self.assertEqual((self.home / registry.REGISTRY).read_bytes(), before)
            command("git", "-C", str(self.content), "rm", name)
            command(
                "git",
                "-C",
                str(self.content),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "test: remove forbidden content",
            )

    def test_restore_and_uninstall_preserve_new_other_machine_records(self):
        self.enroll()
        self.cli("backups", "create")
        _, before_other = self.record()
        path = self.home / registry.REGISTRY
        data = registry.load(self.home)
        local = socket.gethostname()
        other = copy.deepcopy(data["machines"][local])
        other.update(hostname="recorded-later", domain="client", machine_id="1" * 32)
        data["machines"]["recorded-later"] = other
        atomic(path, registry.serialize(data))
        self.terminal(
            ["backups", "restore", "--backup", before_other.name],
            [(b"Confirm [cancel]:", b"restore\n")],
        )
        self.assertEqual(registry.load(self.home)["machines"]["recorded-later"], other)
        self.assertTrue(registry.load(self.home)["machines"][local]["enrolled"])
        self.terminal(
            ["uninstall"],
            [(b"Oldest backup [keep]:", b"keep\n"), (b"Confirm [cancel]:", b"uninstall\n")],
        )
        data = registry.load(self.home)
        self.assertEqual(data["machines"]["recorded-later"], other)
        self.assertFalse(data["machines"][local]["enrolled"])


if __name__ == "__main__":
    unittest.main()
