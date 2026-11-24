"""Check item D, PR 1: the first-enrollment name, the stage record, and the closing check.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_item_d.py --help.
"""

import argparse
import contextlib
import io
import json
import os
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
        import store_migration

        text = (ROOT / "install.sh").read_text()
        for old in store_migration.MERGED:
            self.assertIn(f"$HOME/{old}/profile.toml", text)

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
        path = Path(folder) / "os-release"
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
