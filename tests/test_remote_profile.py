"""Change a connected machine's profile through the existing SSH dispatch.

The target's manager is the only writer of its profile. Its next update applies a saved choice.
Usage: python3 -m unittest discover -s tests -v. No network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_remote_profile.py --help.
"""

import json
import socket
import tomllib
import unittest

import remote
import test_remote


class RemoteProfileRequestTests(unittest.TestCase):
    def test_profile_requests_accept_only_named_choices(self):
        request = remote.profile_request("domain", "set", {"name": "work"})
        self.assertEqual(request["version"], 2)
        for command, action, arguments, message in [
            ("configure", "set", {}, "Only domain, groups, agents, tools, and dotfiles source"),
            ("domain", "remove", {"name": "work"}, "Only domain"),
            ("domain", "set", {"name": "work", "extra": "x"}, "Invalid values"),
            ("domain", "set", {"name": "work; rm -rf ~"}, "Invalid name"),
            ("agents", "set", {"name": "mcp.docs", "state": "on;"}, "Invalid state"),
            ("tools", "set", {"tool": "-x", "version": "1.0.0"}, "Invalid tool"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                remote.profile_request(command, action, arguments)

    def test_version_one_requests_still_validate(self):
        request = {"version": 1, "action": "status", "check": False, "stat": False}
        self.assertEqual(remote.validate_request(request), request)
        with self.assertRaisesRegex(ValueError, "Unsupported remote request version"):
            remote.validate_request(dict(request, version=9))


class RemoteProfileTests(unittest.TestCase):
    setUp = test_remote.RemoteTests.setUp
    connect = test_remote.RemoteTests.connect
    call = test_remote.RemoteTests.call
    caller_files = test_remote.RemoteTests.caller_files
    enroll = test_remote.RemoteTests.enroll
    cli = test_remote.RemoteTests.cli
    record = test_remote.RemoteTests.record

    def target_profile(self):
        return tomllib.loads((self.home / ".fleet/profile.toml").read_text())

    def test_a_remote_content_source_is_saved_on_the_target(self):
        # fleet dotfiles own sends this to each reachable member.
        self.connect()
        new = str(self.base / "mine.git")
        output = self.call("dotfiles", "source", "--repo", new, "--machine", "beelink")
        self.assertIn("dotfiles source succeeded (exit 0).", output)
        self.assertEqual(self.target_profile()["content_repo"], new)

    def test_remote_domain_assignment_saves_then_the_next_update_applies_it(self):
        self.connect()
        before = self.caller_files()
        self.assertIn("Personal shortcut", self.call("domain", "list", "--machine", "beelink"))
        output = self.call("domain", "set", "--name", "personal", "--machine", "beelink")
        # The first contact renamed the member to the name the target reports.
        target = socket.gethostname()
        self.assertIn(f"Machine · {target}", output)
        self.assertIn("Domain core → personal", " ".join(output.split()))
        self.assertIn("next update applies this change", output)
        self.assertIn(f"{target}: domain set succeeded (exit 0).", output)
        self.assertEqual(self.caller_files(), before)
        test_remote.RemoteTests.assert_one_target_member(self)
        self.assertEqual(self.target_profile()["domain"], "personal")
        # Saved, not applied: the target's own files still carry the earlier domain.
        self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), "core\n")
        requests = [json.loads(line)["request"] for line in self.ssh_log.read_text().splitlines()]
        # Each contact ends with a digest exchange; the profile request comes before it.
        request = next(item for item in reversed(requests) if '"version": 2' in item)
        self.assertEqual(
            json.loads(request),
            {
                "version": 2,
                "command": "domain",
                "action": "set",
                "arguments": {"name": "personal"},
            },
        )

        update = self.call("update", "beelink")
        self.assertIn("Update triggered by: profile change.", update)
        self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), "personal\n")
        self.assertIn("No backup taken", self.call("update", "beelink"))

    def test_remote_groups_and_exceptions_are_validated_by_the_target(self):
        self.connect()
        self.call("groups", "set", "--name", "extra", "--state", "off", "--machine", "beelink")
        self.assertFalse(self.target_profile()["groups"]["extra"])
        refused = self.call(
            "groups",
            "set",
            "--name",
            "absent",
            "--state",
            "off",
            "--machine",
            "beelink",
            expected=2,
        )
        self.assertIn("Unknown group absent", refused)
        refused = self.call(
            "agents",
            "set",
            "--name",
            "mcp.docs",
            "--state",
            "off",
            "--machine",
            "beelink",
            expected=2,
        )
        self.assertIn("Unknown agent item mcp.docs", refused)
        refused = self.call("domain", "set", "--name", "work", "--machine", "beelink", expected=2)
        self.assertIn("does not declare domain work", refused)

    def test_the_wizard_and_uninstall_stay_local(self):
        self.connect()
        self.assertIn("configure", self.call("configure", "beelink", expected=2))
        self.assertIn("uninstall", self.call("uninstall", "beelink", expected=2))


if __name__ == "__main__":
    unittest.main()
