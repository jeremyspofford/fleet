"""Exercise SSH routing through a fake executable and a real enrolled target store.

Usage: python3 -m unittest discover -s tests -v. No network connection is used.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_command as commands
from test_manager import ROOT, command
from common import atomic, read_toml, toml
import registry
import remote
import topology
import run_index


class RemoteTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def connect(self):
        self.enroll()
        self.controller = self.base / "controller"
        self.controller.mkdir()
        atomic(self.controller / ".fleet/profile.toml", toml({"git_name": "Caller only"}))
        saved = registry.load(self.home)["machines"][socket.gethostname()].copy()
        saved.update(hostname="beelink", ssh_target="mini-pc")
        atomic(
            self.controller / registry.REGISTRY,
            registry.serialize({"schema_version": 1, "machines": {"beelink": saved}}),
        )
        target_env = self.base / "target-env.json"
        target_env.write_text(json.dumps(self.env))
        self.ssh_log = self.base / "ssh-log.jsonl"
        tools = self.base / "ssh-tools"
        tools.mkdir()
        fake = tools / "ssh"
        fake.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
payload = sys.stdin.read()
with Path(os.environ["FAKE_SSH_LOG"]).open("a") as stream:
    stream.write(json.dumps({"args": sys.argv[1:], "request": payload}) + "\\n")
if os.environ.get("FAKE_SSH_OFFLINE") or "offline-box" in sys.argv:
    print("ssh: connect to host mini-pc port 22: Connection refused", file=sys.stderr)
    print("Connection closed", file=sys.stderr)
    sys.exit(255)
if os.environ.get("FAKE_SSH_NO_FLEET"):
    print("zsh:1: no such file or directory: /home/private/.local/bin/dotfleet", file=sys.stderr)
    sys.exit(1 if sys.argv[-1].startswith("test -x") else 127)
if os.environ.get("FAKE_SSH_EXIT_127"):
    if sys.argv[-1].startswith("test -x"):
        sys.exit(0)
    print("zsh:1: no such file or directory: /home/private/.local/bin/dotfleet", file=sys.stderr)
    sys.exit(127)
environment = json.loads(Path(os.environ["FAKE_TARGET_ENV"]).read_text())
result = subprocess.run(["/bin/sh", "-c", sys.argv[-1]], env=environment,
                        input=payload, text=True, start_new_session=True)
sys.exit(result.returncode)
""")
        fake.chmod(0o700)
        self.controller_env = dict(
            self.env,
            HOME=str(self.controller),
            PATH=str(tools) + ":" + self.env["PATH"],
            XDG_CONFIG_HOME=str(self.controller / ".config"),
            XDG_DATA_HOME=str(self.controller / ".local/share"),
            XDG_STATE_HOME=str(self.controller / ".local/state"),
            FAKE_SSH_LOG=str(self.ssh_log),
            FAKE_TARGET_ENV=str(target_env),
            # The caller is another machine. Its own hostname differs from the target's.
            FLEET_MACHINE_NAME="caller-box",
        )

    def call(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/command.py"), *arguments],
            env=self.controller_env,
            cwd=self.controller,
            text=True,
            capture_output=True,
            timeout=45,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertNotIn("Traceback", output)
        return output

    def caller_files(self):
        """Return the caller's files. Contact may refresh its registry; check that on its own."""
        return {
            str(path.relative_to(self.controller)): path.read_bytes()
            for path in self.controller.rglob("*")
            if path.is_file()
            and path.name not in ("contacts.json", ".contacts.lock", "registry.toml", ".fleet.lock")
            and "remote-runs" not in path.parts
        }

    def assert_one_target_member(self):
        """The caller keeps one record of the target, under the hostname the target reports."""
        machines = registry.load(self.controller)["machines"]
        target = registry.machine_id(self.home)
        found = [key for key, record in machines.items() if record.get("machine_id") == target]
        self.assertEqual(found, [socket.gethostname()])
        self.assertEqual(machines[found[0]]["ssh_target"], "mini-pc")
        self.assertEqual(sorted(machines), [socket.gethostname()])

    def change_content(self):
        (self.content / ".file").write_text("remote content update\n")
        command("git", "-C", str(self.content), "add", ".file")
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
            "fix: remote fixture",
        )

    def test_status_check_diff_and_doctor_use_only_the_target(self):
        self.connect()
        index_before = {path: path.read_bytes() for path in run_index.files(self.home)}
        before = self.caller_files()
        backups = sorted((self.home / ".fleet/backups").iterdir())
        output = self.call("status", "beelink")
        self.assertIn("Machine · beelink", output)
        self.assertIn("Command Example", output)
        self.assertNotIn("Caller only", output)
        self.assertIn("beelink: status succeeded", output)
        self.call("check", "beelink")
        self.call("status", "--check", "beelink", expected=2)
        self.assertIn("No differences.", self.call("diff", "beelink"))
        self.assertIn("PASS", self.call("doctor", "beelink"))
        self.change_content()
        self.assertIn("updates available", self.call("check", "beelink", expected=1))
        diff = self.call("diff", "beelink", expected=1)
        self.assertIn("~/.file", diff)
        self.assertIn("+remote content update", diff)
        stat = self.call("diff", "--stat", "beelink", expected=1)
        self.assertIn("~/.file", stat)
        self.assertNotIn("+remote content update", stat)
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        self.assertEqual(self.caller_files(), before)
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), backups)
        self.assertEqual(
            {path: path.read_bytes() for path in run_index.files(self.home)}, index_before
        )
        for item in map(json.loads, self.ssh_log.read_text().splitlines()):
            if item["args"][-1] == remote.PROBE_COMMAND:
                self.assertEqual(item["request"], "")
                continue
            self.assertEqual(item["args"][-3:], ["--", "mini-pc", remote.REMOTE_COMMAND])
            for option in ("BatchMode=yes", "ConnectTimeout=10", "ConnectionAttempts=1"):
                self.assertIn(option, item["args"])
            request = json.loads(item["request"])
            if request.get("action") == "exchange":
                # Each contact ends with a digest exchange; it carries facts, never commands.
                self.assertEqual(set(request), {"version", "action", "digest"})
                continue
            self.assertEqual(set(request), {"version", "action", "check", "stat"})

    def test_current_remote_update_returns_statement_without_target_backup(self):
        self.connect()
        import updates

        before = {path: path.read_bytes() for path in self.home.glob(".fleet/backups/*/RUN.json")}
        output = self.call("update", "beelink")
        self.assertIn("Machine · beelink", output)
        self.assertIn(updates.CURRENT_MESSAGE, output)
        self.assertNotIn("6 · Next steps", output)
        self.assertEqual(
            {path: path.read_bytes() for path in self.home.glob(".fleet/backups/*/RUN.json")},
            before,
        )
        self.assertTrue((self.home / updates.CACHE).is_file())
        entry = run_index.entries(self.home)[0]
        self.assertTrue(entry["remote"])
        self.assertEqual(entry["result"], "NO_CHANGE")
        self.assertEqual(run_index.entries(self.controller), [])

    def test_update_returns_review_and_writes_only_target_record(self):
        self.connect()
        before = self.caller_files()
        _, previous = self.record()
        self.change_content()
        output = self.call("update", "beelink")
        self.assertIn("Machine · beelink", output)
        self.assertIn("3 · Files", output)
        self.assertIn("~ ~/.file", output)
        self.assertIn("6 · Next steps", output)
        self.assertIn("beelink: update succeeded (exit 0).", output)
        record, folder = self.record()
        self.assertNotEqual(previous, folder)
        self.assertEqual(record["result"], "SUCCESS")
        self.assertTrue(folder.is_relative_to(self.home))
        self.assertEqual((self.home / ".file").read_text(), "remote content update\n")
        self.assertEqual(self.caller_files(), before)
        self.assert_one_target_member()
        self.assert_quiet_status()

    def fleet_home(self):
        """Make this home the caller, with one reachable member and one unreachable member."""
        self.connect()
        path = self.home / registry.REGISTRY
        data = registry.load(self.home)
        own = data["machines"][socket.gethostname()]
        for name, target in (("atlas", "offline-box"), ("beelink", "mini-pc")):
            data["machines"][name] = dict(own, hostname=name, ssh_target=target, enrolled=True)
        atomic(path, registry.serialize(data))
        caller = dict(self.controller_env)
        caller.pop("FLEET_MACHINE_NAME", None)  # This home is the caller now, under its own name.
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            if key in self.env:
                caller[key] = self.env[key]
            else:
                caller.pop(key, None)
        self.controller_env = caller
        self.controller = self.home

    def test_update_all_updates_every_machine_and_continues_past_an_unreachable_one(self):
        self.fleet_home()
        this = socket.gethostname()
        output = self.call("update", "--all", expected=2)
        lines = output.splitlines()
        first = lines.index(f"[1/3] {this}: update succeeded.")
        unreachable = lines.index("[2/3] atlas: unreachable: SSH connection refused.")
        reached = lines.index("[3/3] beelink: update succeeded.")
        self.assertLess(first, unreachable)
        self.assertLess(unreachable, reached)
        self.assertIn("Error: atlas: SSH connection refused.", output)
        self.assertTrue(any(line.startswith("Next: ") for line in lines[first:unreachable]))
        summary = output[output.index("Fleet update · 3 machines") :]
        for name in (this, "atlas", "beelink"):
            self.assertIn(name, summary)
        self.assertIn("unreachable: SSH connection refused", summary)
        targets = [json.loads(line)["args"] for line in self.ssh_log.read_text().splitlines()]
        self.assertEqual(sum("offline-box" in args for args in targets), 1)
        self.assertTrue(any("mini-pc" in args for args in targets))

    def test_update_all_with_a_machine_name_is_an_error(self):
        self.fleet_home()
        before = run_index.entries(self.home)
        output = self.call("update", "beelink", "--all", expected=2)
        self.assertIn("Error: Name one machine or use --all, never both", output)
        self.assertFalse(self.ssh_log.exists())
        self.assertEqual(run_index.entries(self.home), before)

    def test_plain_update_stays_local_with_members_registered(self):
        self.fleet_home()
        import updates

        output = self.call("update")
        self.assertIn(updates.CURRENT_MESSAGE, output)
        self.assertNotIn("[1/", output)
        self.assertNotIn("Fleet update ·", output)
        self.assertFalse(self.ssh_log.exists())

    def test_unreachable_has_next_step_one_attempt_and_private_diagnostics(self):
        self.connect()
        before = self.caller_files()
        self.controller_env["FAKE_SSH_OFFLINE"] = "1"
        output = self.call("status", "beelink", expected=2)
        self.assertEqual(len(output.splitlines()), 2)
        self.assertIn("beelink: SSH connection refused", output)
        self.assertNotIn("Connection closed", output)
        self.assertEqual(len(self.ssh_log.read_text().splitlines()), 1)
        self.assertIn("beelink", topology.load(self.controller)["machines"])
        self.assertEqual(self.caller_files(), before)

    def test_incomplete_target_stops_and_names_all_gaps(self):
        self.connect()
        path = self.home / ".fleet/profile.toml"
        profile = read_toml(path)
        profile.update(git_name="", git_email="", pins={"node": "latest"})
        profile.pop("domain_chosen")
        atomic(path, toml(profile))
        before = path.read_bytes()
        backups = sorted((self.home / ".fleet/backups").iterdir())
        output = self.call("update", "beelink", expected=2)
        for gap in (
            "never explicitly chosen",
            "Git name is empty",
            "Git email is empty",
            "node tool version is floating",
        ):
            self.assertIn(gap, output)
        entry = run_index.entries(self.home)[0]
        self.assertEqual(
            (entry["command"], entry["result"], entry["stage"]), ("update", "FAILED", "profile")
        )
        self.assertTrue(entry["remote"])
        self.assertIn("Git email is empty", entry["error"])
        self.assertIsNone(entry["backup"])
        self.assertIn("Complete these choices on the target", output)
        self.assertNotIn("Confirm", output)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), backups)

    def test_unattended_conflict_stops_without_overwrite(self):
        self.connect()
        (self.home / ".file").write_text("keep target edit\n")
        self.change_content()
        output = self.call("update", "beelink", expected=1)
        self.assertNotIn("Apply stopped", output)
        raw = list(self.controller.glob(".fleet/remote-runs/*/RUN.json"))
        self.assertTrue(any("Apply stopped" in path.read_text() for path in raw))
        self.assertIn("beelink: update failed", output)
        self.assertEqual((self.home / ".file").read_text(), "keep target edit\n")
        self.assertEqual(self.record()[0]["result"], "FAILED")

    def test_missing_command_probe_and_exit_127_have_the_same_safe_guidance(self):
        self.connect()
        for variable, attempts in (("FAKE_SSH_NO_FLEET", 1), ("FAKE_SSH_EXIT_127", 2)):
            with self.subTest(variable=variable):
                self.controller_env.pop("FAKE_SSH_NO_FLEET", None)
                self.controller_env[variable] = "1"
                self.ssh_log.unlink(missing_ok=True)
                output = self.call("status", "beelink", expected=2)
                self.assertEqual(
                    output,
                    "Error: beelink: Fleet is not installed on this machine (or predates the fleet command).\nNext: Run the Fleet one-liner on that machine once, then retry.\n",
                )
                self.assertNotIn("zsh:", output)
                self.assertNotIn("/home/private", output)
                self.assertEqual(len(self.ssh_log.read_text().splitlines()), attempts)
                saved = list(self.controller.glob(".fleet/remote-runs/*/RUN.json"))
                self.assertTrue(saved)
                self.assertTrue(
                    any("zsh:1:" in json.loads(path.read_text())["stderr"] for path in saved)
                )
                for path in saved:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_forbidden_commands_and_unsafe_targets_never_start_ssh(self):
        self.connect()
        for action, reason in (
            ("rollback", "backups restore"),
            ("uninstall", "destructive"),
            ("configure", "terminal"),
        ):
            output = self.call(action, "beelink", expected=2)
            self.assertEqual(len(output.splitlines()), 1)
            self.assertIn(reason, output)
            self.assertFalse(self.ssh_log.exists())
        output = self.call("status", "missing", expected=2)
        self.assertIn("no saved machine", output)
        path = self.controller / registry.REGISTRY
        for target in ("", "-oProxyCommand=bad", "mini-pc; touch /tmp/unsafe", "mini\npc"):
            data = registry.load(self.controller)
            data["machines"]["beelink"]["ssh_target"] = target
            atomic(path, registry.serialize(data))
            self.call("status", "beelink", expected=2)
            self.assertFalse(self.ssh_log.exists())

    def test_set_ssh_creates_records_preserves_facts_and_survives_refresh(self):
        self.enroll()
        from test_connections import fake_ssh

        fake_ssh(self.home, self.env)
        self.assertIn("no connection", self.cli("machines", "list"))
        output = self.cli("machines", "set-ssh", "beelink", "mini-pc")
        self.assertIn("~ ~/.fleet/registry.toml", output)
        self.assertEqual(registry.load(self.home)["machines"]["beelink"]["ssh_target"], "mini-pc")
        local = socket.gethostname()
        before = registry.load(self.home)["machines"][local]
        self.cli("machines", "set-ssh", local, "example@host")
        after = registry.load(self.home)["machines"][local]
        self.assertEqual(after["content_commit"], before["content_commit"])
        self.cli("update")
        self.assertEqual(registry.load(self.home)["machines"][local]["ssh_target"], "example@host")
        # The connection survives the refresh as data; the members-only list omits it.
        self.assertEqual(registry.load(self.home)["machines"]["beelink"]["ssh_target"], "mini-pc")
        self.assertNotIn("beelink", self.cli("machines", "list"))
        self.assertEqual((self.home / registry.REGISTRY).stat().st_mode & 0o777, 0o600)


class RequestTests(unittest.TestCase):
    def test_receiver_refuses_unlisted_commands_and_extra_data_before_dispatch(self):
        request = dict(version=1, action="status", check=False, stat=False)
        invalid = [
            dict(request, action="rollback"),
            dict(request, action="status; touch unsafe"),
            dict(request, machine="nested"),
            dict(request, stat=True),
            dict(request, check="--command"),
            dict(request, version=2),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                remote.validate_request(value)
        with (
            patch("sys.stdin", io.StringIO(json.dumps(invalid[0]))),
            patch("command.main") as dispatch,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(remote.receive(), 2)
            dispatch.assert_not_called()
            self.assertIn("Only status", output.getvalue())


class MemberValidationTests(unittest.TestCase):
    """A newer member may send a field this version does not know."""

    def test_an_unknown_member_field_is_dropped_not_fatal(self):
        member = {
            "hostname": "newer-box",
            "enrolled": True,
            "recorded_at": "2026-11-22T00:00:00+00:00",
            "machine_id": "e" * 32,
            "future_field": "a value from a later Fleet",
        }
        accepted = remote.validate_member(member)
        self.assertNotIn("future_field", accepted)
        self.assertEqual(accepted["hostname"], "newer-box")
        self.assertEqual(accepted["machine_id"], "e" * 32)
        digest = remote.validate_digest({"members": [dict(member)], "removed": {}})
        self.assertEqual(len(digest["members"]), 1)
        self.assertNotIn("future_field", digest["members"][0])
        # A known field with a wrong type still fails.
        with self.assertRaisesRegex(ValueError, "Invalid member"):
            remote.validate_member(dict(member, enrolled="yes"))


if __name__ == "__main__":
    unittest.main()
