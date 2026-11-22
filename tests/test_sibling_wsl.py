"""Enroll and reach a sibling WSL distro on the same Windows host through a fake wsl.exe.

Usage: python3 -m unittest discover -s tests -v. No real distro, wsl.exe, or network is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_sibling_wsl.py --help.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import test_command
import test_deploy

# Each distro is a fixture home. The fake runs a command in the named distro's environment.
FAKE_WSL = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
arguments = sys.argv[1:]
with Path(os.environ["FAKE_WSL_LOG"]).open("a") as log:
    log.write(json.dumps(arguments) + "\n")
homes = json.loads(Path(os.environ["FAKE_WSL_HOMES"]).read_text())
if arguments[:2] == ["--list", "--verbose"]:
    print("  NAME            STATE           VERSION")
    for name in homes:
        mark = "*" if name == os.environ.get("WSL_DISTRO_NAME") else " "
        print(f"{mark} {name}    Running         2")
    sys.exit(0)
distro = arguments[arguments.index("-d") + 1]
command = arguments[-1]
if distro not in homes:
    print("There is no distribution with the supplied name.", file=sys.stderr)
    sys.exit(1)
environment = homes[distro]
result = subprocess.run(["/bin/sh", "-c", command], env=environment, cwd=environment["HOME"])
sys.exit(result.returncode)
"""


def without_wsl(path):
    """Drop PATH entries that hold a real wsl.exe, such as the Windows folders inside WSL."""
    return ":".join(part for part in path.split(":") if not (Path(part) / "wsl.exe").exists())


class SiblingTests(unittest.TestCase):
    setUp = test_deploy.DeployTests.setUp
    enroll = test_deploy.DeployTests.enroll
    prepare = test_deploy.DeployTests.prepare
    call = test_deploy.DeployTests.call
    registry = test_deploy.DeployTests.registry

    def siblings(self, extra=None):
        """Make the caller distro Ubuntu-26.04 and the empty target distro AlertVenture."""
        self.prepare()
        tools = self.base / "wsl-tools"
        tools.mkdir()
        (tools / "wsl.exe").write_text(FAKE_WSL)
        (tools / "wsl.exe").chmod(0o700)
        self.wsl_log = self.base / "wsl.log"
        homes = self.base / "distros.json"
        # Both distros report this host's one host name. Only the saved names tell them apart.
        caller, target = dict(self.caller_env), json.loads((self.base / "homes.json").read_text())
        target = target["mini-pc"]
        for env, distro in ((caller, "Ubuntu-26.04"), (target, "AlertVenture")):
            env.pop("FLEET_MACHINE_NAME", None)
            env.update(
                WSL_DISTRO_NAME=distro,
                FAKE_WSL_LOG=str(self.wsl_log),
                FAKE_WSL_HOMES=str(homes),
                PATH=str(tools) + ":" + without_wsl(env["PATH"]),
            )
        # A distro without Fleet answers, but it holds no Fleet record.
        bare = dict(target, HOME=str(self.base / "debian-home"), WSL_DISTRO_NAME="Debian")
        (self.base / "debian-home").mkdir()
        distros = {"Ubuntu-26.04": caller, "AlertVenture": target, "Debian": bare} | (extra or {})
        homes.write_text(json.dumps(distros))
        self.caller_env = caller

    def install(self, *arguments, expected=0):
        return self.call(
            "machines",
            "install",
            *arguments,
            "--domain",
            "core",
            "--git-name",
            "Target Person",
            "--git-email",
            "target@example.invalid",
            "--repo",
            str(self.content),
            expected=expected,
        )

    def wsl_calls(self):
        if not self.wsl_log.exists():
            return []
        return [json.loads(line) for line in self.wsl_log.read_text().splitlines()]

    def test_two_distros_on_one_host_enroll_distinctly_and_list_each_other(self):
        self.siblings()
        own = (self.home / ".fleet/machine-name").read_text().strip()
        output = self.install("AlertVenture", "--machine-name", "dell-wsl-alertventure")
        self.assertIn("PASS: dell-wsl-alertventure reaches", output)
        caller, target = self.registry(self.home), self.registry(self.target)
        self.assertEqual(sorted(caller), sorted([own, "dell-wsl-alertventure"]))
        self.assertEqual(sorted(target), sorted([own, "dell-wsl-alertventure"]))
        sibling, back = caller["dell-wsl-alertventure"], target[own]
        self.assertEqual(sibling["ssh_target"], "wsl:AlertVenture")
        self.assertEqual(back["ssh_target"], "wsl:Ubuntu-26.04")
        self.assertEqual(sibling["distro"], "AlertVenture")
        # The same host name, two distros, two IDs, and no collision key.
        self.assertEqual(sibling["system_hostname"], socket.gethostname())
        self.assertEqual(target["dell-wsl-alertventure"]["system_hostname"], socket.gethostname())
        self.assertNotEqual(sibling["machine_id"], caller[own]["machine_id"])
        self.assertFalse([key for key in [*caller, *target] if "~" in key])
        # The target met its siblings at enrollment; Debian has no Fleet record, so it is no machine.
        self.assertIn(["-d", "Debian"], [call[:2] for call in self.wsl_calls()])
        self.assertNotIn("Debian", json.dumps([caller, target]))
        self.assertEqual(
            (self.target / ".fleet/machine-name").read_text().strip(), "dell-wsl-alertventure"
        )
        listing = self.call("machines", "list")
        self.assertRegex(listing, r"Connection +wsl:AlertVenture\n")
        self.assertRegex(listing, r"Distro +AlertVenture\n")
        self.assertFalse((self.base / "ssh.log").exists(), "a sibling deploy never uses SSH")

    def test_an_update_dispatched_to_a_sibling_uses_wsl_exe_and_never_ssh(self):
        self.siblings()
        self.install("AlertVenture", "--machine-name", "dell-wsl-alertventure")
        self.wsl_log.unlink()
        output = self.call("update", "dell-wsl-alertventure")
        self.assertIn("dell-wsl-alertventure: update succeeded through wsl.exe (exit 0).", output)
        calls = self.wsl_calls()
        self.assertTrue(calls)
        self.assertTrue(all(call[:2] == ["-d", "AlertVenture"] for call in calls), calls)
        # --exec runs sh directly, so the command's own quoting reaches the distro unchanged.
        self.assertTrue(all(call[4:7] == ["--exec", "sh", "-c"] for call in calls), calls)
        self.assertFalse((self.base / "ssh.log").exists(), "a sibling update never uses SSH")
        everyone = self.call("update", "--all")
        self.assertIn("· sibling dispatch through wsl.exe", everyone)

    def test_a_name_that_is_a_distro_and_an_ssh_host_stops_for_the_flag(self):
        self.siblings(extra={"mini-pc": {"HOME": "/nonexistent"}})
        output = self.install("mini-pc", expected=1)
        self.assertIn("mini-pc names a WSL distro and an SSH config host.", output)
        self.assertIn("Add --via wsl or --via ssh.", output)
        self.assertEqual([call for call in self.wsl_calls() if "-d" in call], [])
        self.assertFalse((self.base / "ssh.log").exists())

    def test_a_scripted_wsl_deploy_needs_a_chosen_name(self):
        self.siblings()
        output = self.install("AlertVenture", expected=1)
        self.assertIn("A scripted WSL deploy needs --machine-name", output)
        self.assertFalse((self.target / ".fleet").exists())


class ExistingEnrollmentTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def test_an_existing_enrollment_backfills_its_distro_and_keeps_its_name(self):
        import registry
        from common import atomic

        self.enroll()
        path = self.home / ".fleet/registry.toml"
        # Make the record an older version's: no saved name and no new facts.
        (self.home / ".fleet/machine-name").unlink()
        data = registry.load(self.home)
        for record in data["machines"].values():
            record.pop("distro", None)
            record.pop("system_hostname", None)
        atomic(path, registry.serialize(data))
        before = tomllib.loads(path.read_text())["machines"]
        (name,) = before
        self.env.update(WSL_DISTRO_NAME="Ubuntu-26.04", PATH=without_wsl(self.env["PATH"]))
        self.cli("check")
        after = tomllib.loads(path.read_text())["machines"]
        self.assertEqual(list(after), [name])
        self.assertEqual(after[name]["distro"], "Ubuntu-26.04")
        self.assertEqual(after[name]["system_hostname"], socket.gethostname())
        facts = ("distro", "system_hostname")
        unchanged = {key: item for key, item in after[name].items() if key not in facts}
        self.assertEqual(unchanged, before[name])
        self.assertEqual((self.home / ".fleet/machine-name").read_text().strip(), name)
        # Another check changes nothing more.
        self.cli("check")
        self.assertEqual(tomllib.loads(path.read_text())["machines"], after)


class InteropTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def test_enrollment_without_interop_succeeds_with_one_notice(self):
        self.env.update(WSL_DISTRO_NAME="AlertVenture", PATH=without_wsl(self.env["PATH"]))
        self.env.pop("FLEET_MACHINE_NAME", None)
        output = self.enroll()
        notice = [line for line in output.splitlines() if "Siblings not checked" in line]
        self.assertEqual(
            notice,
            [
                "Siblings not checked: wsl.exe is unavailable. Next: enable interop in"
                " /etc/wsl.conf ([interop] enabled = true), then restart WSL."
            ],
        )
        machines = tomllib.loads((self.home / ".fleet/registry.toml").read_text())["machines"]
        self.assertEqual(len(machines), 1)
        self.assertEqual(next(iter(machines.values()))["distro"], "AlertVenture")
        self.assertIn("SUCCESS", self.cli("logs", "list"))


# A fake wsl.exe that fails like Windows does: a UTF-16-LE error with an accented letter.
FAILING_WSL = r"""#!/usr/bin/env python3
import os, sys
from pathlib import Path
with Path(os.environ["FAKE_WSL_LOG"]).open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
sys.stderr.buffer.write("Le nom spécifié est introuvable.\r\n".encode("utf-16-le"))
sys.exit(1)
"""


def release(folder, version="24.04"):
    path = Path(folder) / "os-release"
    path.write_text(f'ID=ubuntu\nVERSION_ID="{version}"\n')
    return path


class CorrectionTests(unittest.TestCase):
    """The seven corrections of report F-017. A temporary HOME; the real wsl.exe never runs."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "home"
        (self.home / ".fleet").mkdir(parents=True)
        self.folder = Path(folder.name)
        environment = patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("FLEET_MACHINE_NAME", None)

    def save_registry(self, machines):
        import registry
        from common import atomic

        data = {"schema_version": 1, "machines": {}, "removed": {}}
        for name, fields in machines.items():
            record = registry.blank(name) | {"enrolled": True} | fields
            data["machines"][name] = record
        atomic(self.home / registry.REGISTRY, registry.serialize(data))

    def install_args(self, name=None, target=None, via=None):
        return argparse.Namespace(
            name=name, ssh_target=target, via=via, machine_name=None, return_target=None
        )

    def wsl_install(self, args, distros, hosts=(), terminal=True):
        import command
        import siblings

        with (
            patch.object(siblings, "interop", return_value=True),
            patch.object(siblings, "distros", return_value=list(distros)),
            patch.object(siblings, "own_distro", return_value="Ubuntu-A"),
            patch.object(command.connections, "hosts", return_value=list(hosts)),
            patch.object(command.manager, "has_terminal", return_value=terminal),
        ):
            return command.wsl_install(self.home, args)

    # 1
    def test_machine_name_flag_never_renames_an_enrollment_without_a_saved_name(self):
        import registry
        import run_index

        (self.home / ".fleet/profile.toml").write_text('domain = "core"\n')
        with self.assertRaisesRegex(ValueError, "already named .*Fleet never renames it"):
            registry.save_name(self.home, "new")
        self.assertFalse((self.home / run_index.NAME_FILE).exists())
        self.assertEqual(run_index.machine_name(), socket.gethostname())
        # The current name is accepted and saved.
        registry.save_name(self.home, socket.gethostname())
        self.assertEqual(run_index.saved_name(self.home), socket.gethostname())

    # 2
    def test_a_bare_target_keeps_its_ssh_meaning(self):
        args = self.install_args("box", "debian")
        self.assertFalse(self.wsl_install(args, ["Debian"], hosts=["debian"]))
        self.assertEqual(args.ssh_target, "debian")

    # 3
    def test_a_saved_ssh_target_and_a_distro_stop_for_via(self):
        self.save_registry({"debian": {"ssh_target": "me@debian.lan", "enrolled": False}})
        with self.assertRaisesRegex(
            ValueError,
            "debian names a WSL distro and a saved SSH target. Add --via wsl or --via ssh.",
        ):
            self.wsl_install(self.install_args("debian"), ["Debian"])

    # 4
    def wsl_record(self):
        import registry

        self.save_registry(
            {
                "dell-wsl": {
                    "machine_id": "a" * 32,
                    "distro": "AlertVenture",
                    "system_hostname": "DELL-XPS-8950",
                    "recorded_at": "2026-11-22T00:00:00+00:00",
                }
            }
        )
        return registry.digest(self.home)

    def test_a_digest_never_carries_local_facts(self):
        members = self.wsl_record()["members"]
        self.assertTrue(members)
        for member in members:
            self.assertNotIn("distro", member)
            self.assertNotIn("system_hostname", member)

    def test_a_digest_from_this_version_validates_under_the_base_rules(self):
        # The field set that remote.validate_member accepted at 604fc0a, before this PR.
        import registry

        base = set(registry.FIELDS) | {"ssh_target", "recorded_at", "machine_id", "aliases"}
        for member in self.wsl_record()["members"]:
            self.assertLessEqual(set(member), base, member)

    # 5
    def test_a_wsl_target_without_name_deploys_and_lets_the_target_name_itself(self):
        args = self.install_args(None, "wsl:Ubuntu-B")
        self.assertTrue(self.wsl_install(args, ["Ubuntu-A", "Ubuntu-B"], terminal=True))
        self.assertIsNone(args.machine_name)
        self.assertEqual(args.name, "Ubuntu-B")
        self.assertEqual(args.ssh_target, "wsl:Ubuntu-B")

    # 6
    def test_proposed_names_follow_the_rule(self):
        import registry

        examples = [
            ("DELL-XPS-8950", "alertventure", "26.04", "dell-ubuntu-26.04-alertventure"),
            ("DELL-XPS-8950", "Ubuntu-24.04", "24.04", "dell-ubuntu-24.04"),
            ("DELL-XPS-8950", "Ubuntu", "24.04", "dell-ubuntu-24.04-ubuntu"),
            ("DESKTOP-AAAA111", "Ubuntu-24.04", "24.04", "desktop-aaaa111-ubuntu-24.04"),
            ("DESKTOP-BBBB222", "Ubuntu-24.04", "24.04", "desktop-bbbb222-ubuntu-24.04"),
        ]
        for host, distro, version, expected in examples:
            with self.subTest(host=host, distro=distro):
                name = registry.proposed_name(distro, release(self.folder, version), host)
                self.assertEqual(name, expected)

    def test_a_taken_proposed_name_gets_a_suffix(self):
        import registry

        self.save_registry({"dell-ubuntu-24.04": {"machine_id": "b" * 32}})
        name = registry.proposed_name(
            "Ubuntu-24.04", release(self.folder), "DELL-XPS-8950", home=self.home
        )
        self.assertEqual(name, "dell-ubuntu-24.04-2")

    # 7
    def test_sibling_environment_passes_wsl_utf8_to_windows(self):
        import connections

        with patch.dict(os.environ, {"WSLENV": "FOO"}):
            self.assertEqual(connections.environment(sibling=True)["WSLENV"], "FOO:WSL_UTF8")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WSLENV", None)
            self.assertEqual(connections.environment(sibling=True)["WSLENV"], "WSL_UTF8")

    def test_a_utf16_error_from_wsl_exe_becomes_a_deploy_error(self):
        import deploy
        import remote
        import siblings

        tools = self.folder / "tools"
        tools.mkdir()
        (tools / "wsl.exe").write_text(FAILING_WSL)
        (tools / "wsl.exe").chmod(0o700)
        log = self.folder / "wsl.log"
        variables = {
            "PATH": str(tools) + ":" + without_wsl(os.environ["PATH"]),
            "FAKE_WSL_LOG": str(log),
            "WSL_DISTRO_NAME": "Ubuntu-A",
        }
        with patch.dict(os.environ, variables):
            with self.assertRaises(deploy.DeployError) as caught:
                deploy.facts_of("Bad", "wsl:Bad")
            self.assertIn("spécifié", " ".join(caught.exception.lines))
            # Registration moves on to the next distro instead of stopping.
            with patch.object(siblings, "distros", return_value=["Bad", "Next"]):
                self.assertEqual(siblings.register(self.home), [])
            calls = log.read_text()
            self.assertIn("-d Next", calls)
            # A sibling update reports the member and returns, so update --all continues.
            self.save_registry({"bad-box": {"ssh_target": "wsl:Bad", "machine_id": "c" * 32}})
            report = {}
            code = remote.execute(self.home, "bad-box", "update", report=report)
        self.assertNotEqual(code, 0)
        self.assertTrue(report["result"])


if __name__ == "__main__":
    unittest.main()
