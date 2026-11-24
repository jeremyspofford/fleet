"""Exercise enrollment doors, review, provenance, and flags through a real terminal.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_wizard.py --help.
"""

import json
import os
from pathlib import Path
import subprocess
import tomllib
import unittest

import dotfiles_library

import test_manager as helpers
from convert import manifest_path
from test_manager import ROOT, command, repository
from common import toml
from enrollment import scan


class WizardTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status
    seed_profile = helpers.EnrollmentTests.seed_profile

    def answers(self, door="example", repo=None):
        result = [(b"Content source [", (door + "\n").encode())]
        if repo:
            result.append((b"Content repository [", (str(repo) + "\n").encode()))
        result += [
            (b"Domain [core]:", b"\n"),
            (b"Git author name [", b"Wizard Person\n"),
            (b"Git author email [", b"wizard@example.invalid\n"),
            (b"Tools [accept]:", b"\n"),
        ]
        if door != "adopt" and repo != os.environ["DOTFLEET_ARCHIVE_FIXTURE"]:
            result += [
                (f"{name} [on]:".encode(), b"\n")
                for name in list(
                    tomllib.loads(
                        (manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))).read_text()
                    )["groups"]
                )
            ]
        result += [(b"Review [confirm]:", b"confirm\n")]
        return result

    def defaults_origin(self):
        # Exercise the real defaults URL anonymously without downloading during each test.
        self.env.update(
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_1=f"url.{os.environ['DOTFLEET_CONTENT_FIXTURE']}.insteadOf",
            GIT_CONFIG_VALUE_1="https://github.com/jeremyspofford/dotfiles",
        )

    def snapshot(self):
        result = {}
        for path in self.home.rglob("*"):
            name = str(path.relative_to(self.home))
            if name in (".local", ".local/share", ".fleet") and name not in getattr(
                self, "prior_directories", set()
            ):
                continue
            if name == ".fleet/runs" or name.startswith(".fleet/runs/"):
                continue
            # The install stage record keeps a chosen name for the next run, like the run index.
            if name == ".fleet/install-stages.json":
                continue
            value = (
                ("link", os.readlink(path))
                if path.is_symlink()
                else ("dir",)
                if path.is_dir()
                else ("file", path.read_bytes())
            )
            result[name] = (value, path.lstat().st_mode & 0o777)
        return result

    def test_defaults_door_bare_home_and_silent_rerun(self):
        self.defaults_origin()
        output = self.terminal(self.answers(), flags=[], auto_wizard=False)
        self.assertIn(
            "  example  Start from Fleet's example shell and Git files. (Recommended.)", output
        )
        self.assertIn("7. Content groups", output)
        self.assertIn("Group: docker NEW", output)
        self.assertNotIn("7. Group:", output)
        self.assertIn("3 · Files", output)
        self.assertIn("will be", output)
        self.assertIn("chezmoi status: clean", output)
        self.assert_quiet_status()
        # The example source downloads the starter pack as default and activates it.
        self.assertEqual(
            [path.name for path in (self.home / ".fleet/dotfiles").iterdir()], ["default"]
        )
        saved = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(saved["dotfiles"], "default")
        profile = (self.home / ".fleet/profile.toml").read_bytes()
        output = self.terminal([], flags=[], auto_wizard=False)
        self.assertNotIn("9. Review", output)
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), profile)
        self.assert_quiet_status()

    def test_adopt_excludes_secret_and_preserves_every_adopted_byte(self):
        (self.home / ".zshrc").write_bytes(b"# lived-in shell\nexport ADOPTED=yes\n")
        (self.home / ".aliases").write_bytes(b"alias here='pwd'\n")
        (self.home / ".settings.example").write_text("Preserve and manage this existing file.\n")
        (self.home / ".linked").symlink_to(".aliases")
        folder = self.home / ".config/example"
        folder.mkdir(parents=True)
        (folder / "settings.ini").write_bytes(b"layout = compact\n")
        secret = self.home / ".service-config"
        secret.write_bytes(b'export SERVICE_API_TOKEN="fake-secret-for-this-test"\n')
        before = {name: (self.home / name).read_bytes() for name in scan(self.home)[0]}
        output = self.terminal(self.answers("adopt"), flags=[], auto_wizard=False)
        self.assertIn("Press Enter alone to choose adopt.", output)
        self.assertIn("Sensitive files are excluded.", output)
        self.assertNotIn("fake-secret-for-this-test", output)
        checkout = dotfiles_library.active(self.home)
        self.assertEqual(checkout.name, "adopted")
        names = command("git", "-C", str(checkout), "ls-files").splitlines()
        self.assertNotIn(".service-config", names)
        self.assertEqual(set(names), set(before) - {".service-config"} | {"fleet.toml"})
        for name in set(names) - {"fleet.toml"}:
            self.assertEqual((self.home / name).read_bytes(), before[name], name)
        self.assertEqual(secret.read_bytes(), before[".service-config"])
        managed = command(
            "chezmoi", "managed", "--path-style", "relative", env=self.env, cwd=self.home
        ).splitlines()
        self.assertIn(".settings.example", managed)
        self.assertEqual(command("git", "-C", str(checkout), "remote"), "")
        self.assert_quiet_status()
        self.terminal([], flags=[], auto_wizard=False)
        self.assert_quiet_status()

    def test_repo_door_legacy_stow(self):
        repo = os.environ["DOTFLEET_ARCHIVE_FIXTURE"]
        output = self.terminal(self.answers("repo", repo), flags=[], auto_wizard=False)
        self.assertIn("chezmoi status: clean", output)
        self.assertTrue((self.home / ".zshrc").is_file())
        self.assert_quiet_status()

    def test_review_quit_keeps_home_files_and_records_cancellation(self):
        self.defaults_origin()
        (self.home / ".zshrc").write_text("# keep exactly\n")
        (self.home / ".link").symlink_to(".zshrc")
        self.prior_directories = {
            str(path.relative_to(self.home)) for path in self.home.rglob("*") if path.is_dir()
        }
        before = self.snapshot()
        answers = self.answers()
        answers[-1] = (b"Review [confirm]:", b"quit\n")
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn("chezmoi status: cancelled", output)
        self.assertEqual(self.snapshot(), before)

    def test_enter_alone_at_review_confirms_and_typed_quit_is_required_to_cancel(self):
        self.defaults_origin()
        answers = self.answers()
        answers[-1] = (b"Review [confirm]:", b"q\n")
        answers += [(b"Choose confirm, quit,", b"\n")]
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn("  confirm   Back up files, then apply these choices. (Recommended.)", output)
        self.assertIn("Press Enter alone to choose confirm.", output)
        self.assertEqual(output.count("5. Git commit identity"), 1)
        self.assertIn("chezmoi status: clean", output)
        self.assertTrue((self.home / ".fleet/profile.toml").is_file())
        self.assert_quiet_status()

    def test_first_wsl_enrollment_asks_for_and_saves_the_machine_name(self):
        self.defaults_origin()
        self.env["WSL_DISTRO_NAME"] = "AlertVenture"
        self.env.pop("FLEET_MACHINE_NAME", None)
        (self.home / ".fleet/machine-name").unlink()
        answers = [
            (b"Machine name [", b"bad name!\n"),
            (b"Machine name [", b"dell-wsl-alertventure\n"),
        ] + self.answers()
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn("Use letters, digits, dots, hyphens, and underscores.", output)
        self.assertEqual(
            (self.home / ".fleet/machine-name").read_text().strip(), "dell-wsl-alertventure"
        )
        registry = tomllib.loads((self.home / ".fleet/registry.toml").read_text())
        machines = registry.get("machines", registry)
        self.assertEqual(machines["dell-wsl-alertventure"]["distro"], "AlertVenture")

    def test_the_name_prompt_rejects_another_machines_name(self):
        import registry
        from common import atomic

        self.defaults_origin()
        self.env["WSL_DISTRO_NAME"] = "AlertVenture"
        self.env.pop("FLEET_MACHINE_NAME", None)
        (self.home / ".fleet/machine-name").unlink()
        other = registry.blank("other-box") | {"enrolled": True, "machine_id": "d" * 32}
        data = {"schema_version": 1, "machines": {"other-box": other}, "removed": {}}
        atomic(self.home / registry.REGISTRY, registry.serialize(data))
        answers = [
            (b"Machine name [", b"other-box\n"),
            (b"Machine name [", b"my-box\n"),
        ] + self.answers()
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn("other-box belongs to another machine. Choose another name.", output)
        self.assertEqual((self.home / ".fleet/machine-name").read_text().strip(), "my-box")

    def test_review_go_back_changes_identity_before_apply(self):
        self.defaults_origin()
        answers = self.answers()
        answers[-1] = (b"Review [confirm]:", b"identity\n")
        answers += [
            (b"Git author name [Wizard Person]:", b"Changed Person\n"),
            (b"Git author email [wizard@example.invalid]:", b"\n"),
            (b"Tools [accept]:", b"\n"),
        ]
        answers += [
            (f"{name} [on]:".encode(), b"\n")
            for name in list(
                tomllib.loads(
                    (manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))).read_text()
                )["groups"]
            )
        ]
        answers += [(b"Review [confirm]:", b"confirm\n")]
        self.terminal(answers, flags=[], auto_wizard=False)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["git_name"], "Changed Person")
        self.assert_quiet_status()

    def test_carried_noncore_domain_still_requires_human_choice(self):
        path = self.seed_profile(legacy=True, complete=True)
        profile = tomllib.loads(path.read_text())
        profile["domain"] = "personal"
        path.write_text(toml(profile))
        args = ["bash", str(ROOT / "install.sh"), "--defaults"]
        failed = subprocess.run(
            args,
            env=self.env,
            cwd=self.home,
            capture_output=True,
            text=True,
            start_new_session=True,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "Domain was never explicitly chosen (recorded: personal)", failed.stdout + failed.stderr
        )
        self.assertFalse((self.home / ".fleet/dotfiles").exists())
        self.terminal(
            [
                (b"Domain [personal]:", b"personal\n"),
                (b"Git author name [", b"\n"),
                (b"Git author email [", b"\n"),
            ],
            flags=[],
        )
        recorded = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertTrue(recorded["domain_chosen"])
        self.terminal([], flags=[], auto_wizard=False)

    def test_scripted_flags_select_groups_and_agents_without_prompts(self):
        args = [
            "bash",
            str(ROOT / "install.sh"),
            "--repo",
            os.environ["DOTFLEET_CONTENT_FIXTURE"],
            "--defaults",
            "--domain",
            "core",
            "--git-name",
            "Flag Person",
            "--git-email",
            "flag@example.invalid",
            "--group",
            "docker=off",
            "--agents",
            "none",
            "--tool",
            "node=24.14.1",
        ]
        output = command(*args, env=self.env, cwd=self.home)
        self.assertNotIn("Review [", output)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertFalse(profile["groups"]["docker"])
        self.assertFalse(any(profile["agents"].values()))
        self.assertNotIn(
            "dotfleet:group docker", (self.home / ".config/dotfiles/aliases/core.zsh").read_text()
        )
        self.assert_quiet_status()

    def test_review_abort_with_missing_tools_keeps_home_unchanged(self):
        self.defaults_origin()
        self.env["PATH"] = "/usr/bin:/bin"
        (self.home / ".zshrc").write_text("# preserve before tool downloads\n")
        self.prior_directories = {
            str(path.relative_to(self.home)) for path in self.home.rglob("*") if path.is_dir()
        }
        before = self.snapshot()
        answers = self.answers()
        answers[-1] = (b"Review [confirm]:", b"quit\n")
        output = self.terminal(answers, flags=[], auto_wizard=False, timeout=120)
        self.assertIn("Preparing chezmoi", output)
        self.assertIn("Preparing Mise", output)
        self.assertEqual(self.snapshot(), before)

    def test_agent_selection_renders_reviewed_entries_and_content_skills(self):
        content = self.base / "agents-content"
        command("git", "clone", "-q", os.environ["DOTFLEET_CONTENT_FIXTURE"], str(content))
        manifest = manifest_path(content)
        manifest.write_text(
            manifest.read_text()
            + '\n[agents]\nschema = 1\n[agents.mcp.docs]\ndescription = "Search docs."\n'
            + 'transport = "stdio"\ncommand = "npx"\n'
        )
        skill = content / ".agents/skills/review-change/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: review-change\ndescription: Review.\n---\n")
        command("git", "-C", str(content), "add", ".")
        command(
            "git",
            "-C",
            str(content),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "-m",
            "feat: agents",
        )
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        executable = bin_dir / "opencode"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + ":" + self.env["PATH"]
        answers = self.answers("repo", content)
        answers.insert(-1, (b"opencode [off]:", b"on\n"))
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn(".config/opencode/opencode.json", output)
        settings = json.loads((self.home / ".config/opencode/opencode.json").read_text())
        self.assertIn("mcp", settings)
        self.assertTrue((self.home / ".agents/skills/review-change/SKILL.md").is_file())
        backup = next((self.home / ".fleet/backups").iterdir())
        inventory = json.loads((backup / "MANIFEST.json").read_text())
        self.assertIn(".config/opencode/opencode.json", {item["path"] for item in inventory})
        self.assert_quiet_status()
        self.terminal([], flags=[], auto_wizard=False)

    def test_unknown_format_is_asked_and_recorded(self):
        repo = repository(self.base / "unknown", {"README.md": "No home files yet."})
        answers = [
            (b"Content source [", b"repo\n"),
            (b"Content repository [", (str(repo) + "\n").encode()),
            (b"Format [plain]:", b"plain\n"),
            (b"Domain [core]:", b"\n"),
            (b"Git author name [", b"Wizard Person\n"),
            (b"Git author email [", b"wizard@example.invalid\n"),
            (b"Tools [accept]:", b"\n"),
            (b"Review [confirm]:", b"confirm\n"),
        ]
        self.terminal(answers, flags=[], auto_wizard=False)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["format"], "plain")
        self.terminal([], flags=[], auto_wizard=False)
        self.assert_quiet_status()

    def test_adopt_existing_enrollment_preserves_previous_checkout(self):
        self.defaults_origin()
        self.terminal(self.answers(), flags=[], auto_wizard=False)
        previous_commit = command(
            "git", "-C", str(dotfiles_library.active(self.home)), "rev-parse", "HEAD"
        ).strip()
        before = (self.home / ".zshrc").read_bytes()
        answers = self.answers("adopt")
        answers[1] = (b"Domain [core]:", b"\n")
        self.terminal(answers, flags=["--configure"], auto_wizard=False)
        self.assertEqual((self.home / ".zshrc").read_bytes(), before)
        # The earlier download stays in the library at its commit; adoption adds its own.
        library = self.home / ".fleet/dotfiles"
        self.assertEqual(sorted(path.name for path in library.iterdir()), ["adopted", "default"])
        self.assertEqual(
            command("git", "-C", str(library / "default"), "rev-parse", "HEAD").strip(),
            previous_commit,
        )
        self.assertEqual(dotfiles_library.active(self.home).name, "adopted")
        self.assert_quiet_status()

    def test_secret_scan_checks_names_assignments_and_links(self):
        for name, data in {
            ".env": b"harmless\n",
            ".config-one": b"OPENAI_API_KEY=FAKEKEY123456\n",
            ".config-two": b"password: not-a-real-password\n",
            ".config-three": b"-----BEGIN OPENSSH PRIVATE KEY-----\n",
        }.items():
            (self.home / name).write_bytes(data)
        (self.home / ".linked").symlink_to(".config-one")
        found, excluded = scan(self.home)
        self.assertEqual(set(found), set(excluded))


if __name__ == "__main__":
    unittest.main()
