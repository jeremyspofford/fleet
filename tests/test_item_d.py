"""Check item D, PR 1: the first-enrollment name, the stage record, and the closing check.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_item_d.py --help.
"""

import argparse
import contextlib
import io
import json
import os
import re
import signal
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import manager
import registry
import run_index
import test_manager as helpers
import test_wizard
from test_manager import ROOT

PROMPT = b"Machine name ["


def other_version(version):
    """Return another version with the same shape as VERSION, such as 99.99 for 26.04."""
    nines = ".".join("9" * len(part) for part in version.split("."))
    return nines if nines != version else ".".join("1" * len(part) for part in version.split("."))


def files(home):
    return {
        str(path.relative_to(home)): path.read_bytes()
        for path in sorted(home.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


class FirstNameTests(unittest.TestCase):
    """Part 1: every first enrollment asks for a name; a scripted one needs --machine-name."""

    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    install = helpers.EnrollmentTests.install
    answers = test_wizard.WizardTests.answers
    defaults_origin = test_wizard.WizardTests.defaults_origin

    def fresh(self):
        """Make this home a new machine: no saved name and no name override."""
        (self.home / run_index.NAME_FILE).unlink()
        self.env.pop("FLEET_MACHINE_NAME", None)
        self.env.pop("WSL_DISTRO_NAME", None)

    def scripted(self, *extra):
        arguments = [
            "bash",
            str(ROOT / "install.sh"),
            "--repo",
            os.environ["DOTFLEET_CONTENT_FIXTURE"],
        ]
        arguments += ["--defaults", "--domain", "core", "--git-name", "Item D"]
        arguments += ["--git-email", "item-d@example.invalid", *extra]
        return subprocess.run(
            arguments,
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=300,
        )

    def test_a_linux_first_enrollment_asks_once_and_enter_accepts_the_proposal(self):
        self.defaults_origin()
        self.fresh()
        proposal = registry.proposed_name(distro="", system="Linux")
        output = self.terminal([(PROMPT, b"\n")] + self.answers(), flags=[], auto_wizard=False)
        self.assertEqual(output.count("Machine name ["), 1)
        self.assertIn(f"Machine name [{proposal}]", output)
        self.assertEqual(run_index.saved_name(self.home), proposal)
        machines = registry.load(self.home)["machines"]
        self.assertEqual(list(machines), [proposal])
        # Part 4: the run ends with fleet check and the three-line section.
        # The result depends on the fixture's upstream: current, behind, or a failed fetch.
        self.assertEqual(output.count("7 · Enrollment"), 1)
        self.assertRegex(output, r"7 · Enrollment\r?\n  \S*fleet check(: | failed: )")
        for label in ("Automated", "Skipped", "Manual"):
            self.assertRegex(output, rf"\n  {label} +\S")
        stages = run_index.install_stages(self.home)
        self.assertLessEqual({"detect", "software", "enrollment"}, set(stages))
        self.assertEqual(stages["detect"]["name"], proposal)
        # A second run never asks, and the name and registry key stay.
        second = self.install(defaults=True)
        self.assertNotIn("Machine name [", second)
        self.assertEqual(run_index.saved_name(self.home), proposal)
        self.assertEqual(list(registry.load(self.home)["machines"]), [proposal])

    def test_a_typed_name_is_saved_and_a_wrong_version_is_asked_again(self):
        self.defaults_origin()
        self.fresh()
        facts = registry.release_facts()
        if not facts.get("ID") or not facts.get("VERSION_ID"):
            self.skipTest("This host's os-release has no distro version to contradict.")
        wrong = (
            f"box-{registry.sanitized(facts['ID'])}-{other_version(facts['VERSION_ID'])}".encode()
        )
        answers = [(PROMPT, wrong + b"\n"), (PROMPT, b"typed-box\n")] + self.answers()
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn("but this machine runs", output)
        self.assertEqual(run_index.saved_name(self.home), "typed-box")

    def test_a_scripted_first_enrollment_needs_a_name_and_writes_nothing(self):
        self.fresh()
        before = files(self.home)
        result = self.scripted()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "A first enrollment needs a machine name. Add --machine-name NAME, such as",
            result.stdout + result.stderr,
        )
        self.assertEqual(files(self.home), before)
        facts = registry.release_facts()
        if facts.get("ID") and facts.get("VERSION_ID"):
            wrong = f"box-{registry.sanitized(facts['ID'])}-{other_version(facts['VERSION_ID'])}"
            rejected = self.scripted("--machine-name", wrong)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("but this machine runs", rejected.stdout + rejected.stderr)
            self.assertIsNone(run_index.saved_name(self.home) or None)
        named = self.scripted("--machine-name", "scripted-box")
        self.assertEqual(named.returncode, 0, named.stdout + named.stderr)
        self.assertEqual(run_index.saved_name(self.home), "scripted-box")
        self.assertEqual(list(registry.load(self.home)["machines"]), ["scripted-box"])

    def test_an_enrolled_machine_on_an_old_store_layout_is_not_stopped(self):
        # Review F-036, defect 1: Fleet 0.8 kept the profile in ~/.config/fleet.
        self.install()
        old = self.home / ".config/fleet/profile.toml"
        old.parent.mkdir(parents=True)
        (self.home / ".fleet/profile.toml").rename(old)
        (self.home / run_index.NAME_FILE).unlink()
        self.env.pop("FLEET_MACHINE_NAME", None)
        result = self.scripted()
        output = result.stdout + result.stderr
        self.assertNotIn("A first enrollment needs a machine name", output)
        self.assertEqual(result.returncode, 0, output)
        self.assertTrue((self.home / ".fleet/profile.toml").is_file())

    def test_the_installer_knows_every_old_store_that_migration_merges(self):
        # The early name stop must find a profile in every store that migration moves later.
        import store_migration

        text = (ROOT / "install.sh").read_text()
        loop = re.search(r"^for profile in (.*?); do$", text, re.M | re.S)
        self.assertIsNotNone(loop, "install.sh has no `for profile in ...; do` list")
        listed = set(re.findall(r'"\$HOME/([^"]+)/profile\.toml"', loop.group(1)))
        expected = set(store_migration.MERGED) | {".fleet", ".config/dotfiles"}
        self.assertEqual(listed, expected)

    def test_macos_and_windows_propose_their_system_and_ask_once(self):
        for system, suffix in (("Darwin", "macos"), ("Windows", "windows")):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                asked = []

                def ask(label, default, *arguments, **options):
                    asked.append(default)
                    return default

                args = argparse.Namespace(machine_name=None)
                with (
                    patch.dict(os.environ, {"HOME": folder}),
                    patch.object(registry.platform, "system", return_value=system),
                    patch.object(registry.socket, "gethostname", return_value="DELL-XPS-8950"),
                    patch.object(manager, "has_terminal", return_value=True),
                    patch.object(manager, "ask", side_effect=ask),
                ):
                    os.environ.pop("FLEET_MACHINE_NAME", None)
                    os.environ.pop("WSL_DISTRO_NAME", None)
                    self.assertFalse(manager.choose_name(home, args, True))
                self.assertEqual(asked, [f"dell-{suffix}"])
                self.assertEqual(run_index.saved_name(home), f"dell-{suffix}")


class VersionRuleTests(unittest.TestCase):
    """Review F-036, defect 2: a taken-name suffix such as -2 is not a version."""

    def release(self, folder, distro, version):
        path = Path(folder) / f"{distro}-{version}-os-release"
        path.write_text(f'ID={distro}\nVERSION_ID="{version}"\n')
        return path

    def test_a_taken_name_suffix_is_not_read_as_a_version(self):
        with tempfile.TemporaryDirectory() as folder:
            ubuntu = self.release(folder, "ubuntu", "24.04")
            self.assertEqual(registry.version_conflict("dell-ubuntu-2", ubuntu), "")
            self.assertEqual(registry.version_conflict("dell-ubuntu-24.04-2", ubuntu), "")
            self.assertIn("ubuntu 22.04", registry.version_conflict("dell-ubuntu-22.04", ubuntu))
            fedora = self.release(folder, "fedora", "41")
            self.assertEqual(registry.version_conflict("dell-fedora-2", fedora), "")
            self.assertIn("fedora 40", registry.version_conflict("dell-fedora-40", fedora))

    def test_a_real_other_release_with_more_parts_is_rejected(self):
        # Review F-042: each name below names a real other release of this machine's distro.
        cases = [
            ("rocky", "9.4", "dell-rocky-8.10"),
            ("rocky", "9.4", "dell-rocky-9.10"),
            ("ubuntu", "24.04", "dell-ubuntu-9.04"),
            ("ubuntu", "24.04", "dell-ubuntu-22.04.5"),
            ("alpine", "3.20.3", "dell-alpine-3.19"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            for distro, version, name in cases:
                with self.subTest(name=name):
                    release = self.release(folder, distro, version)
                    self.assertIn("but this machine runs", registry.version_conflict(name, release))

    def test_a_one_part_version_reads_a_shorter_number_as_a_suffix(self):
        # On Debian 12, -9 can be a taken-name suffix, so the rule accepts dell-debian-9.
        with tempfile.TemporaryDirectory() as folder:
            debian = self.release(folder, "debian", "12")
            self.assertEqual(registry.version_conflict("dell-debian-9", debian), "")
            self.assertIn("debian 11", registry.version_conflict("dell-debian-11", debian))

    def test_a_dotted_number_is_a_version_for_a_one_part_release_too(self):
        # Review F-044, edge 3: a suffix is never dotted, so 11.5 can only be a version.
        with tempfile.TemporaryDirectory() as folder:
            debian = self.release(folder, "debian", "12")
            self.assertIn("debian 11.5", registry.version_conflict("dell-debian-11.5", debian))

    def test_a_more_or_less_precise_form_of_this_release_is_accepted(self):
        # Review F-044, edge 5: each name names the running release, more or less precisely.
        with tempfile.TemporaryDirectory() as folder:
            ubuntu = self.release(folder, "ubuntu", "24.04")
            self.assertEqual(registry.version_conflict("dell-ubuntu-24.04.1", ubuntu), "")
            alpine = self.release(folder, "alpine", "3.20.3")
            self.assertEqual(registry.version_conflict("dell-alpine-3.20", alpine), "")
            debian = self.release(folder, "debian", "12")
            self.assertEqual(registry.version_conflict("dell-debian-12.8", debian), "")
            # A shared first part alone is not this release.
            self.assertIn("alpine 3.2", registry.version_conflict("dell-alpine-3.2", alpine))
            self.assertIn("ubuntu 24.10", registry.version_conflict("dell-ubuntu-24.10", ubuntu))

    def test_a_proposal_skips_a_suffix_that_reads_as_another_release(self):
        # Review F-044, edge 4: -10 and -11 read as Debian 10 and 11 on Debian 12.
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            debian = self.release(folder, "debian", "12")
            taken = ["dell-debian"] + [f"dell-debian-{number}" for number in range(2, 10)]
            machines = {
                name: dict(registry.blank(name), machine_id=f"{index:032x}")
                for index, name in enumerate(taken, 1)
            }
            (home / ".fleet").mkdir()
            (home / registry.REGISTRY).write_text(
                registry.serialize({"schema_version": 1, "machines": machines})
            )
            name = registry.proposed_name("", debian, "DELL-XPS", home, "Linux")
            self.assertEqual(name, "dell-debian-12")
            self.assertEqual(registry.version_conflict(name, debian), "")

    def test_an_upgraded_wsl_distro_drops_its_old_release_from_the_proposal(self):
        # Review F-051, Spec 4: an in-place upgrade keeps the WSL distro's old name.
        with tempfile.TemporaryDirectory() as folder:
            ubuntu = self.release(folder, "ubuntu", "26.04")
            cases = {
                "Ubuntu-24.04": "dell-ubuntu-26.04",
                "Work-Ubuntu-24.04": "dell-ubuntu-26.04-work-ubuntu",
                "Ubuntu-26.04": "dell-ubuntu-26.04",
                "AlertVenture": "dell-ubuntu-26.04-alertventure",
            }
            for distro, expected in cases.items():
                with self.subTest(distro=distro):
                    name = registry.proposed_name(distro, ubuntu, "DELL", None, "WSL")
                    self.assertEqual(name, expected)

    def test_every_proposal_passes_the_version_rule(self):
        # Fleet must never propose a name that its own prompt then rejects.
        hosts = ["DELL-XPS", "DESKTOP-UBUNTU-22.04", "laptop-debian-11", "box"]
        scopes = ["", "Ubuntu-22.04", "Debian-11.5", "Fedora-40-Lab", "Alpine-3.19", "lab"]
        releases = [("ubuntu", "24.04"), ("debian", "12"), ("fedora", "41"), ("alpine", "3.20.3")]
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".fleet").mkdir()
            for distro, version in releases:
                release = self.release(folder, distro, version)
                for host in hosts:
                    for scope in scopes:
                        for system in ("WSL", "Linux"):
                            base = registry.proposed_name(scope, release, host, None, system)
                            taken = [base] + [f"{base}-{number}" for number in range(2, 15)]
                            machines = {
                                name: dict(registry.blank(name), machine_id=f"{index:032x}")
                                for index, name in enumerate(taken, 1)
                            }
                            (home / registry.REGISTRY).write_text(
                                registry.serialize({"schema_version": 1, "machines": machines})
                            )
                            for where in (None, home):
                                name = registry.proposed_name(scope, release, host, where, system)
                                with self.subTest(release=release.name, host=host, scope=scope):
                                    self.assertEqual(registry.version_conflict(name, release), "")

    def test_chained_releases_are_all_removed_and_the_proposal_ends(self):
        # Review F-056: removing one release can expose the next one.
        cases = [
            ("debian", "12", "", "LAPTOP-DEBIAN-11-10", "Linux"),
            ("debian", "12", "Debian", "LAPTOP-DEBIAN-11-10", "WSL"),
            ("debian", "12", "Debian-11-10-13", "DELL", "WSL"),
            ("fedora", "41", "", "DESKTOP-FEDORA-40-39", "Linux"),
            ("ubuntu", "24.04", "Ubuntu-22.04-23.10", "DELL", "WSL"),
            (
                "opensuse-tumbleweed",
                "20240101",
                "openSUSE-Tumbleweed-20230101-20231201",
                "DELL",
                "WSL",
            ),
        ]

        def hang(signum, frame):
            raise TimeoutError("proposed_name() did not finish within 5 seconds")

        previous = signal.signal(signal.SIGALRM, hang)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        with tempfile.TemporaryDirectory() as folder:
            for distro, version, scope, host, system in cases:
                release = self.release(folder, distro, version)
                with self.subTest(host=host, scope=scope, system=system):
                    signal.alarm(5)
                    try:
                        name = registry.proposed_name(scope, release, host, None, system)
                    finally:
                        signal.alarm(0)
                    self.assertEqual(registry.version_conflict(name, release), "")

    def test_the_suffix_loop_stops_with_a_clear_error_at_its_limit(self):
        # A defect that rejects every suffix must stop the proposal, not hang it.
        def every_suffix_conflicts(name, release):
            return "" if name == "dell-debian" else f"{name} names another release."

        with tempfile.TemporaryDirectory() as folder:
            debian = self.release(folder, "debian", "12")
            with (
                patch.object(registry, "version_conflict", every_suffix_conflicts),
                patch.object(registry, "taken_names", return_value={"dell-debian"}),
            ):
                with self.assertRaisesRegex(ValueError, "no free machine name after 130 tries"):
                    registry.proposed_name("", debian, "DELL", Path(folder), "Linux")


class ResumeTests(unittest.TestCase):
    """Part 2: a re-run skips a completed detect stage and asks no name."""

    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    answers = test_wizard.WizardTests.answers
    defaults_origin = test_wizard.WizardTests.defaults_origin
    fresh = FirstNameTests.fresh

    def test_a_run_stopped_after_detect_resumes_without_a_name_prompt(self):
        self.defaults_origin()
        self.fresh()
        stopped = self.answers()
        stopped[-1] = (b"Review [confirm]:", b"quit\n")
        self.terminal([(PROMPT, b"resume-box\n")] + stopped, flags=[], auto_wizard=False)
        self.assertEqual(set(run_index.install_stages(self.home)), {"detect"})
        self.assertFalse((self.home / ".fleet/profile.toml").exists())
        output = self.terminal(self.answers(), flags=[], auto_wizard=False)
        self.assertNotIn("Machine name [", output)
        self.assertIn("Detect: done earlier; machine name resume-box.", output)
        self.assertIn("machine name, chosen earlier", output)
        # The final state equals one uninterrupted run.
        self.assertEqual(run_index.saved_name(self.home), "resume-box")
        self.assertEqual(list(registry.load(self.home)["machines"]), ["resume-box"])
        stages = run_index.install_stages(self.home)
        self.assertLessEqual({"detect", "software", "enrollment"}, set(stages))


class DetectLineTests(unittest.TestCase):
    """Review F-036, defect 3: the resume line belongs to a first enrollment only."""

    def test_an_update_after_enrollment_prints_no_detect_line(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".fleet").mkdir()
            (home / run_index.NAME_FILE).write_text("box\n")
            run_index.complete_stage(home, "detect", {"name": "box"})
            args = argparse.Namespace(machine_name=None)
            for first, expected in (
                (False, ""),
                (True, "Detect: done earlier; machine name box.\n"),
            ):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertFalse(manager.choose_name(home, args, first))
                self.assertEqual(output.getvalue(), expected)


class StageRecordTests(unittest.TestCase):
    """Part 2: the record is private, survives corruption, and leaves with uninstall."""

    def test_a_corrupt_record_warns_once_and_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            path = home / run_index.STAGES_FILE
            path.parent.mkdir(parents=True)
            path.write_text("{not json")
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(run_index.install_stages(home), {})
            self.assertEqual(error.getvalue().count("Warning:"), 1)
            run_index.complete_stage(home, "detect", {"name": "box"})
            self.assertEqual(json.loads(path.read_text())["stages"]["detect"]["name"], "box")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_uninstall_removes_the_record(self):
        import command

        self.assertIn(run_index.STAGES_FILE, command.CONTROLS)


class VerifyTests(unittest.TestCase):
    """Part 4: a failed check leaves the enrollment complete and names the command."""

    def test_a_failed_check_names_the_failure_and_the_command(self):
        import updates

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)

            def failing(*arguments, **options):
                print(
                    "Warning: Could not check content: the remote did not answer.",
                    file=os.sys.stderr,
                )
                return 2

            with patch.object(updates, "check", side_effect=failing):
                result = manager.verify_enrollment(home, {})
            self.assertIn("check failed: Warning: Could not check content", result["check"])
            self.assertIn("Repeat it with", result["check"])
            self.assertEqual(result["manual"], ["dotfleet check"])
            stages = run_index.install_stages(home)
            self.assertIn("enrollment", stages)
            self.assertNotIn("verify", stages)


if __name__ == "__main__":
    unittest.main()
