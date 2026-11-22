"""Exercise real conversion, enrollment, updates, and saved machine settings.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_manager.py --help.
"""

import json
import re
import hashlib
import errno
import fcntl
import pty
import select
import signal
import termios
import time
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest

import dotfiles_library
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Tests see a plain Linux host, as CI does. WSL tests set the distro name themselves.
os.environ.pop("WSL_DISTRO_NAME", None)
sys.path.insert(0, str(ROOT / "scripts"))
from manager import domain_details, exact_version, installed_defaults, validate_profile
from common import toml
from convert import manifest_path, convert, detect


def command(*args, env=None, cwd=None):
    result = subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    if args[0] == "bash" and len(args) > 1 and args[1] == str(ROOT / "install.sh"):
        assert "config file template has changed" not in result.stdout + result.stderr, (
            result.stdout + result.stderr
        )
    return result.stdout


def repository(path, files):
    path.mkdir()
    command("git", "init", "-b", "main", str(path))
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    command("git", "-C", str(path), "add", ".")
    command(
        "git",
        "-C",
        str(path),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "test: fixture",
    )
    return path


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        # A test home starts with its name chosen, like a machine that stopped after naming.
        # Tests of the first-enrollment name prompt and stop remove this file.
        (self.home / ".fleet").mkdir()
        (self.home / ".fleet/machine-name").write_text(socket.gethostname() + "\n")
        runtime = os.environ.get("FLEET_TEST_TUI_ENV")
        if runtime:
            # Cold installation has its own test. Other fixtures reuse its private dependencies.
            shutil.copytree(runtime, self.home / ".fleet/tui", symlinks=True)
        # Host agent commands must not affect a fixture's detected agents.
        tools = self.base / "tools"
        tools.mkdir()
        for name in ("chezmoi", "mise"):
            location = shutil.which(name)
            if location:
                (tools / name).symlink_to(location)
        self.env = dict(
            os.environ,
            PATH=str(tools) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            HOME=str(self.home),
            SHELL="/bin/bash",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="safe.directory",
            GIT_CONFIG_VALUE_0="*",
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            XDG_STATE_HOME=str(self.home / ".local/state"),
            MISE_TRUSTED_CONFIG_PATHS=str(self.home),
        )
        for key in list(self.env):
            if key.startswith(("CHEZMOI_", "MISE_")) and key != "MISE_TRUSTED_CONFIG_PATHS":
                self.env.pop(key)

    def install(self, repo=None, defaults=True):
        args = ["bash", str(ROOT / "install.sh")]
        if repo:
            args += ["--repo", str(repo)]
        if defaults:
            args += ["--defaults"]
        existing = [
            self.home / name
            for name in [
                ".fleet/profile.toml",
                ".config/dotfiles/profile.toml",
                ".config/chezmoi/chezmoi.toml",
            ]
        ]
        if not any(path.exists() for path in existing):
            args += [
                "--domain",
                "core",
                "--git-name",
                "Fixture Person",
                "--git-email",
                "fixture@example.invalid",
            ]
        return command(*args, env=self.env, cwd=self.home)

    def complete_profile(self):
        path = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(path.read_text())
        profile.update(
            domain_chosen=True, git_name="Fixture Person", git_email="fixture@example.invalid"
        )
        path.write_text(toml(profile))

    def assert_quiet_status(self):
        result = subprocess.run(
            ["bash", "-c", "umask 0022; chezmoi status"],
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def assert_clean(self):
        self.assert_quiet_status()
        self.assertEqual(
            command("git", "-C", str(dotfiles_library.active(self.home)), "status", "--porcelain"),
            "",
        )
        self.assertEqual((self.home / ".secrets").stat().st_mode & 0o777, 0o600)
        for shell in ["bash", "zsh"]:
            env = dict(self.env, SHELL=f"/bin/{shell}")
            master, slave = pty.openpty()

            def controlling_terminal():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                [shell, "-lic", "printf shell-ok"],
                cwd=self.home,
                env=env,
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=controlling_terminal,
            )
            os.close(slave)
            try:
                stdout, stderr = process.communicate(timeout=20)
            finally:
                os.close(master)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, "")
            self.assertTrue(stdout.endswith("shell-ok"))

    def exercise(self, repo):
        (self.home / ".zshrc").write_text("# existing configuration\n")
        external = self.base / "external"
        external.write_text("# external aliases\n")
        (self.home / ".aliases").symlink_to(external)
        output = self.install(repo)
        self.assertIn("chezmoi status: clean", output)
        self.assert_clean()
        backups = list((self.home / ".fleet/backups").iterdir())
        saved = backups[0] / "home"
        self.assertEqual((saved / ".zshrc").read_text(), "# existing configuration\n")
        if (self.home / ".aliases").exists():
            self.assertTrue((saved / ".aliases").is_symlink())
            self.assertEqual(
                (backups[0] / "external" / str(external).lstrip("/")).read_bytes(),
                external.read_bytes(),
            )
        state = self.home / ".fleet/outputs.json"
        before = state.read_bytes()
        # Complete the deliberately defaulted profile before testing unattended updates.
        self.complete_profile()
        self.install(defaults=False)
        before = state.read_bytes()
        profile = (self.home / ".fleet/profile.toml").read_bytes()
        self.assertIn("chezmoi status: clean", self.install(defaults=False))
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), profile)
        self.assert_clean()

    def test_fresh_install_and_rerun_have_no_config_warning(self):
        self.assertIn(
            "chezmoi status: clean", self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        )
        self.assert_quiet_status()
        self.assertIn("chezmoi status: clean", self.install())
        self.assert_quiet_status()

    def test_legacy_template_state_is_retired_without_resetting_other_state(self):
        legacy = self.base / "legacy"
        contents = toml(
            {
                "sourceDir": str(legacy),
                "umask": 63,
                "data": {
                    "domain": "personal",
                    "gitName": "Fixture Person",
                    "gitEmail": "fixture@example.invalid",
                },
            }
        )
        repository(legacy, {".chezmoi.toml.tmpl": contents, "dot_zshrc": "# legacy\n"})
        command("chezmoi", "--source", str(legacy), "init", env=self.env, cwd=self.home)
        command("chezmoi", "apply", env=self.env, cwd=self.home)
        cached = command(
            "chezmoi", "state", "get", "--bucket=configState", "--key=configState", env=self.env
        )
        self.assertEqual(
            json.loads(cached)["configTemplateContentsSHA256"],
            hashlib.sha256(contents.encode()).hexdigest(),
        )
        command(
            "chezmoi",
            "state",
            "set",
            "--bucket=scriptState",
            "--key=keep",
            '--value={"keep": true}',
            env=self.env,
        )
        command(
            "bash",
            str(ROOT / "install.sh"),
            "--repo",
            os.environ["DOTFLEET_CONTENT_FIXTURE"],
            "--defaults",
            "--domain",
            "personal",
            env=self.env,
            cwd=self.home,
        )
        self.assert_quiet_status()
        self.assertEqual(
            command(
                "chezmoi", "state", "get", "--bucket=configState", "--key=configState", env=self.env
            ),
            "",
        )
        self.assertEqual(
            json.loads(
                command(
                    "chezmoi", "state", "get", "--bucket=scriptState", "--key=keep", env=self.env
                )
            ),
            {"keep": True},
        )
        # Repair the same stale marker on an already enrolled machine, as on the owners' machines.
        command(
            "chezmoi",
            "state",
            "set",
            "--bucket=configState",
            "--key=configState",
            "--value=" + cached,
            env=self.env,
        )
        warning = subprocess.run(
            ["chezmoi", "status"], env=self.env, text=True, capture_output=True
        )
        self.assertIn("config file template has changed", warning.stderr)
        self.install(defaults=False)
        self.assert_clean()

    def test_native_config_template_is_acknowledged_without_running_it(self):
        contents = '{{ fail "The manager must own the config" }}\n'
        repo = repository(
            self.base / "native-template",
            {".chezmoi.toml.tmpl": contents, "dot_zshrc": "# native\n"},
        )
        self.install(repo)
        config = self.home / ".config/chezmoi/chezmoi.toml"
        before = config.read_bytes()
        self.assert_quiet_status()
        self.install()
        self.assertEqual(config.read_bytes(), before)
        self.assert_quiet_status()
        # Updating a native template must not reintroduce the warning or take ownership.
        (repo / ".chezmoi.toml.tmpl").write_text(contents + "# updated\n")
        command("git", "-C", str(repo), "add", ".")
        command(
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "test: update template",
        )
        self.install()
        self.assertEqual(config.read_bytes(), before)
        self.assert_quiet_status()

    def test_plain_content_and_groups(self):
        repo = Path(os.environ["DOTFLEET_CONTENT_FIXTURE"])
        self.exercise(repo)
        path = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(path.read_text())
        aliases = self.home / ".config/dotfiles/aliases/core.zsh"
        original = aliases.read_text()
        profile["groups"]["docker"] = False
        path.write_text(toml(profile))
        self.install(defaults=False)
        self.assertNotIn("dotfleet:group docker", aliases.read_text())
        self.assertIn("dotfleet:group kubectl", aliases.read_text())
        self.assert_clean()
        profile["groups"]["docker"] = True
        path.write_text(toml(profile))
        self.install(defaults=False)
        self.assertEqual(aliases.read_text(), original)
        self.assert_clean()

    def test_real_legacy_stow_archive(self):
        self.exercise(Path(os.environ["DOTFLEET_ARCHIVE_FIXTURE"]))

    def test_native_source(self):
        repo = repository(
            self.base / "native",
            {
                "dot_zshrc": "# native zsh\n",
                "dot_bashrc": "# native bash\n",
                "dot_aliases": "# native aliases\n",
                ".chezmoidata.toml": "example = true\n",
            },
        )
        self.exercise(repo)
        config = tomllib.loads((self.home / ".config/chezmoi/chezmoi.toml").read_text())
        self.assertEqual(config["sourceDir"], str(dotfiles_library.active(self.home)))

    def test_migration_reads_all_sources_and_preserves_pins(self):
        for name, data in {
            ".config/chezmoi/chezmoi.toml": {
                "data": {
                    "domain": "personal",
                    "gitName": "Example Owner",
                    "gitEmail": "owner@example.invalid",
                    "miseTools": {"node": "22.1.0", "python": "3.12.0"},
                }
            },
            ".config/mise/config.toml": {
                "tools": {"node": "24.1.0"},
                "settings": {"experimental": False},
            },
            ".config/dotfiles/profile.toml": {"pins": {"python": "3.13.0"}},
        }.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(toml(data))
        (self.home / ".secrets").write_text("# preserved secret placeholder\n")
        output = command(
            "bash",
            str(ROOT / "install.sh"),
            "--repo",
            os.environ["DOTFLEET_CONTENT_FIXTURE"],
            "--defaults",
            "--domain",
            "personal",
            env=self.env,
            cwd=self.home,
        )
        self.assertIn("old chezmoi data, current Mise tools, saved installer profile", output)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["pins"], {"node": "24.1.0", "python": "3.13.0"})
        self.assertEqual(profile["git_name"], "Example Owner")
        rendered = tomllib.loads((self.home / ".config/mise/conf.d/fleet.toml").read_text())
        self.assertEqual(rendered["settings"], {"experimental": False})
        # Every value moved into Fleet's fragment, so the old global file went to the backup.
        self.assertFalse((self.home / ".config/mise/config.toml").exists())
        self.assertIn("Mise: Fleet's pins moved to ~/.config/mise/conf.d/fleet.toml.", output)
        before = (self.home / ".fleet/profile.toml").read_bytes()
        self.install(defaults=False)
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), before)
        self.assertEqual((self.home / ".secrets").read_text(), "# preserved secret placeholder\n")
        self.assertEqual(command("chezmoi", "status", env=self.env, cwd=self.home), "")

    def terminal(
        self,
        responses,
        expected=0,
        configure=False,
        plain=False,
        raw=False,
        flags=None,
        auto_wizard=True,
        timeout=45,
    ):
        master, slave = pty.openpty()

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        args = ["bash", str(ROOT / "install.sh"), "--repo", os.environ["DOTFLEET_CONTENT_FIXTURE"]]
        if plain:
            args = ["bash", str(ROOT / "install.sh")]
        if flags is not None:
            args = ["bash", str(ROOT / "install.sh"), *flags]
        if configure:
            args.append("--configure")
        process = subprocess.Popen(
            args,
            cwd=self.home,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=slave,
            preexec_fn=controlling_terminal,
        )
        os.close(slave)
        process.stdin.write(b"all-overwrite\nWRONG PERSON\n")
        process.stdin.close()
        transcript = b""
        cursor = 0
        answered = 0
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    transcript += chunk
                elif process.poll() is not None:
                    break
                if answered < len(responses) and responses[answered][0] in re.sub(
                    rb"\x1b\[[0-9;]*m", b"", transcript[cursor:]
                ):
                    os.write(master, responses[answered][1])
                    cursor = len(transcript)
                    answered += 1
                elif auto_wizard:
                    pending = re.sub(rb"\x1b\[[0-9;]*m", b"", transcript[cursor:])
                    for pattern, answer in [
                        (rb"Content source \[[^]]+\]:", b"repo\n"),
                        (rb"Content repository \[[^]]+\]:", b"\n"),
                        (rb"Tools \[accept\]:", b"accept\n"),
                        (rb"(?:claude|codex|gemini|grok|pi|opencode) \[(?:on|off)\]:", b"\n"),
                        (rb"Review \[confirm\]:", b"confirm\n"),
                        (
                            rb"(?:docker|terraform|kubectl|python|npm|helpers) \[(?:on|off)\]:",
                            b"\n",
                        ),
                    ]:
                        if re.search(pattern, pending):
                            os.write(master, answer)
                            cursor = len(transcript)
                            break
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            os.close(master)
        raw_output = transcript.decode(errors="replace")
        output = re.sub(r"\x1b\[[0-9;]*m", "", raw_output)
        self.assertEqual(process.returncode, expected, output)
        self.assertEqual(answered, len(responses), output)
        self.assertNotIn("diff/overwrite/all-overwrite/skip/Skip-conflicts/quit", output)
        return raw_output if raw else output

    def seed_profile(self, *, legacy=False, complete=False, installed=True):
        profile = {
            "content_repo": os.environ["DOTFLEET_CONTENT_FIXTURE"],
            "domain": "core",
            "git_name": "Fixture Person" if complete else "",
            "git_email": "fixture@example.invalid" if complete else "",
            "pins": {"node": "24.14.1" if complete else "latest"},
            "groups": {
                group: True
                for group in list(
                    tomllib.loads(
                        (manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))).read_text()
                    )["groups"]
                )
            },
        }
        if complete:
            profile["domain_chosen"] = True
        name = ".config/dotfiles/profile.toml" if legacy else ".fleet/profile.toml"
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(toml(profile))
        if installed:
            node = self.home / ".local/share/mise/installs/node/24.14.1/bin/node"
            node.parent.mkdir(parents=True)
            node.write_text("#!/bin/sh\nprintf 'v24.14.1\\n'\n")
            node.chmod(0o755)
        return path

    def gap_answers(self):
        return [
            (b"[core]:", b"\n"),
            (b"Git author name", b"Fixture Person\n"),
            (b"Git author email", b"fixture@example.invalid\n"),
            (b"[latest]:", b"\n"),
        ]

    def check_completed_gap_profile(self):
        path = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(path.read_text())
        self.assertTrue(profile["domain_chosen"])
        self.assertEqual(profile["domain"], "core")
        self.assertEqual(profile["git_name"], "Fixture Person")
        self.assertEqual(profile["git_email"], "fixture@example.invalid")
        self.assertEqual(profile["pins"], {"node": "24.14.1"})
        self.assert_quiet_status()
        before = path.read_bytes()
        self.terminal([], plain=True)
        self.assertEqual(path.read_bytes(), before)

    def test_migration_prompts_for_all_gaps_and_records_installed_exact_version(self):
        self.seed_profile(legacy=True)
        output = self.terminal(self.gap_answers(), plain=True)
        self.assertIn("Press Enter alone: Fleet uses installed 24.14.1.", output)
        self.assertIn("Recorded exact version: node = 24.14.1", output)
        self.check_completed_gap_profile()

    def test_plain_rerun_repairs_existing_profile_gaps(self):
        self.seed_profile()
        self.terminal(self.gap_answers(), plain=True)
        self.check_completed_gap_profile()

    def test_complete_migration_with_explicit_domain_needs_no_prompts(self):
        self.seed_profile(legacy=True, complete=True)
        output = command(
            "bash", str(ROOT / "install.sh"), "--domain", "core", env=self.env, cwd=self.home
        )
        self.assertNotIn("Git author", output)
        self.assertNotIn("Domain:", output)
        self.assertNotIn("node version:", output)
        self.assertIn("warnings: none", output)
        self.check_completed_gap_profile()

    def test_skipped_gaps_warn_and_do_not_repin_from_content_defaults(self):
        self.seed_profile()
        answers = [
            (b"[core]:", b"skip\n"),
            (b"Git author name", b"skip\n"),
            (b"Git author email", b"skip\n"),
            (b"[latest]:", b"skip\n"),
        ]
        output = self.terminal(answers, plain=True)
        self.assertIn("Warnings:", output)
        self.assertIn("No domain choice confirmed", output)
        self.assertIn("No complete git identity recorded", output)
        self.assertIn("node has no selected version", output)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertFalse(profile["domain_chosen"])
        self.assertNotIn("node", profile["pins"])
        self.assertNotIn(
            "node",
            tomllib.loads((self.home / ".config/mise/conf.d/fleet.toml").read_text())["tools"],
        )
        # Skipped identity/domain gaps remain visible on the next plain run.
        self.terminal(self.gap_answers()[:3], plain=True)
        self.assert_quiet_status()

    def test_uninstalled_floating_pin_accepts_exact_version_or_stays_unpinned(self):
        self.seed_profile(installed=False)
        answers = self.gap_answers()[:3] + [(b"[latest]:", b"24\n"), (b"[latest]:", b"24.14.1\n")]
        output = self.terminal(answers, plain=True)
        self.assertIn("no matching version is installed", output)
        self.check_completed_gap_profile()

    def test_noninteractive_gaps_fail_before_apply_even_with_defaults(self):
        path = self.seed_profile()
        before = path.read_bytes()
        for flags in [[], ["--defaults"]]:
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh"), *flags],
                env=self.env,
                cwd=self.home,
                input="personal\nWrong Name\nwrong@example.invalid\n",
                text=True,
                capture_output=True,
                start_new_session=True,
            )
            self.assertNotEqual(result.returncode, 0)
            for text in [
                "Domain was never explicitly chosen (recorded: core)",
                "Git name is empty",
                "Git email is empty",
                "node tool version is floating (recorded: latest)",
                "--domain",
                "--git-name",
                "--git-email",
                "--tool node=VERSION",
            ]:
                self.assertIn(text, result.stdout + result.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((self.home / ".fleet/dotfiles").exists())
            self.assertFalse((self.home / ".config/chezmoi/chezmoi.toml").exists())
            self.assertFalse((self.home / ".fleet/backups").exists())

    def test_noninteractive_flags_complete_the_profile_without_prompts(self):
        self.seed_profile(installed=False)
        args = [
            "bash",
            str(ROOT / "install.sh"),
            "--domain",
            "personal",
            "--git-name",
            "Script Person",
            "--git-email",
            "script@example.invalid",
            "--tool",
            "node=24.14.1",
            "--tool",
            "python=3.13.0",
        ]
        result = subprocess.run(
            args,
            env=self.env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for prompt in [
            "Git author name;",
            "Git author email;",
            "Domain:",
            "node version:",
            "Press Enter",
        ]:
            self.assertNotIn(prompt, result.stdout + result.stderr)
        self.assertIn("warnings: none", result.stdout)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["domain"], "personal")
        self.assertTrue(profile["domain_chosen"])
        self.assertEqual(profile["git_name"], "Script Person")
        self.assertEqual(profile["git_email"], "script@example.invalid")
        self.assertEqual(profile["pins"], {"node": "24.14.1", "python": "3.13.0"})
        self.assert_quiet_status()
        self.install(defaults=False)

    def test_noninteractive_fresh_install_with_flags_prints_group_defaults(self):
        args = [
            "bash",
            str(ROOT / "install.sh"),
            "--repo",
            os.environ["DOTFLEET_CONTENT_FIXTURE"],
            "--domain",
            "core",
            "--git-name",
            "Script Person",
            "--git-email",
            "script@example.invalid",
        ]
        result = subprocess.run(
            args,
            env=self.env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Group docker: using on", result.stdout)
        self.assertIn("warnings: none", result.stdout)
        self.assert_quiet_status()
        self.install(defaults=False)

    def test_profile_flags_reject_missing_and_invalid_arguments(self):
        cases = [
            [flag]
            for flag in ["--domain", "--git-name", "--git-email", "--tool", "--group", "--agents"]
        ]
        cases += [
            ["--domain", "Unknown_Domain"],
            ["--tool", "node"],
            ["--group", "docker=maybe"],
            ["--repo", "example", "--adopt"],
        ]
        for flags in cases:
            with self.subTest(flags=flags):
                result = subprocess.run(
                    ["bash", str(ROOT / "install.sh"), *flags],
                    env=self.env,
                    cwd=self.home,
                    text=True,
                    capture_output=True,
                    start_new_session=True,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("usage:", (result.stdout + result.stderr).lower())
                self.assertFalse((self.home / ".fleet/profile.toml").exists())

    def test_noninteractive_partial_flags_still_name_remaining_gaps(self):
        self.seed_profile(installed=False)
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--domain", "personal", "--tool", "node=latest"],
            env=self.env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git name is empty", result.stdout + result.stderr)
        self.assertIn("Git email is empty", result.stdout + result.stderr)
        self.assertIn("node tool version is floating", result.stdout + result.stderr)
        self.assertNotIn("Domain was never explicitly chosen", result.stdout)

    def test_terminal_profile_and_update(self):
        responses = [
            (b"[core]:", b"invalid\n"),
            (b"[core]:", b"personal\n"),
            (b"Git author name", b"Fixture Person\n"),
            (b"Git author email", b"fixture@example.invalid\n"),
        ]
        responses += [
            (f"{group} [".encode(), b"\n")
            for group in list(
                tomllib.loads(
                    (manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))).read_text()
                )["groups"]
            )
        ]
        self.terminal(responses)
        path = self.home / ".fleet/profile.toml"
        profile = path.read_bytes()
        self.assertEqual(tomllib.loads(profile.decode())["git_name"], "Fixture Person")
        self.terminal([])
        self.assertEqual(path.read_bytes(), profile)
        self.assert_clean()

    def test_first_domain_prompt_explains_choices_and_saves_core_default(self):
        answers = [
            (b"Domain [core]:", b"\n"),
            (b"Git author name", b"Fixture Person\n"),
            (b"Git author email", b"fixture@example.invalid\n"),
        ]
        answers += [
            (f"{group} [".encode(), b"\n")
            for group in list(
                tomllib.loads(
                    (manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))).read_text()
                )["groups"]
            )
        ]
        output = self.terminal(answers)
        for description in [
            "Base configuration only. No domain additions.",
            "Choosing a domain does not install software or create accounts.",
            "Your choice is saved in ~/.fleet/profile.toml.",
            "Type a domain name, or press Enter alone to choose core.",
            "  skip      Keep core for now without confirming it as your choice.",
        ]:
            self.assertIn(description, output)
        self.assertNotIn("personal=personal+shared", output)
        path = self.home / ".fleet/profile.toml"
        saved = path.read_bytes()
        profile = tomllib.loads(saved.decode())
        self.assertEqual(profile["domain"], "core")
        self.assertTrue(profile["domain_chosen"])
        self.terminal([], plain=True)
        self.assertEqual(path.read_bytes(), saved)
        self.assert_clean()

    def test_colored_prompts_use_logger_palette_and_plain_text_labels(self):
        self.seed_profile()
        self.env.pop("NO_COLOR", None)
        self.env["TERM"] = "xterm-256color"
        output = self.terminal(
            [(b"Domain [core]:", b"invalid\n"), *self.gap_answers()], plain=True, raw=True
        )
        self.assertIn("\x1b[36m\x1b[1m  4. Choose your domain", output)
        self.assertIn("\x1b[32mDomain [core]:", output)
        self.assertIn("\x1b[33mChoose core, personal, work, client, skip.", output)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output).replace("\r\n", "\n")
        self.assertIn("\n" + __import__("style").section("4. Choose your domain") + "\n", plain)
        self.assertIn("\n\nDomain [core]:", plain)
        self.assertIn(__import__("style").section("5. Git commit identity"), plain)
        self.assertIn("Base configuration only. No domain additions.", plain)
        self.assert_quiet_status()

    def test_no_color_and_basic_terminal_keep_prompts_readable(self):
        path = self.seed_profile(complete=True)
        self.install(defaults=False)
        for no_color, term in [("", "xterm-256color"), ("1", "xterm-256color"), (None, "dumb")]:
            with self.subTest(no_color=no_color, term=term):
                self.env.pop("NO_COLOR", None)
                if no_color is not None:
                    self.env["NO_COLOR"] = no_color
                self.env["TERM"] = term
                answers = [
                    (b"Domain [core]:", b"\n"),
                    (b"Git author name", b"\n"),
                    (b"Git author email", b"\n"),
                ]
                answers += [
                    (f"{group} [".encode(), b"\n")
                    for group in list(
                        tomllib.loads(
                            (
                                manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
                            ).read_text()
                        )["groups"]
                    )
                ]
                before = path.read_bytes()
                output = self.terminal(answers, configure=True, raw=True)
                self.assertNotIn("\x1b[", output)
                self.assertIn("4. Choose your domain", output)
                self.assertIn("Domain [core]:", output)
                self.assertEqual(path.read_bytes(), before)
        # Captured output must remain plain even when TERM supports color.
        self.env.pop("NO_COLOR", None)
        self.env["TERM"] = "xterm-256color"
        self.assertNotIn("\x1b[", self.install(defaults=False))

    def test_configure_defaults_to_each_saved_domain_and_keeps_tool_choices(self):
        path = self.seed_profile(complete=True)
        self.install(defaults=False)
        for domain in ["personal", "work", "client", "core"]:
            with self.subTest(domain=domain):
                profile = tomllib.loads(path.read_text())
                profile["domain"] = domain
                path.write_text(toml(profile))
                before = path.read_bytes()
                answers = [
                    (f"Domain [{domain}]:".encode(), b"\n"),
                    (b"Git author name", b"\n"),
                    (b"Git author email", b"\n"),
                ]
                answers += [
                    (f"{group} [".encode(), b"\n")
                    for group in list(
                        tomllib.loads(
                            (
                                manifest_path(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
                            ).read_text()
                        )["groups"]
                    )
                ]
                self.terminal(answers, configure=True)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), domain + "\n")
                self.assert_quiet_status()

    def test_terminal_conflict_decisions(self):
        self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        self.complete_profile()
        paths = [self.home / ".aliases", self.home / ".bashrc"]
        original = [path.read_bytes() for path in paths]
        for path in paths:
            path.write_bytes(path.read_bytes() + b"\n# local edit\n")
        edited = [path.read_bytes() for path in paths]
        prompt = b"Choose [d/o/a/s/S/q]:"
        for answer in [b"S\n", b"q\n"]:
            self.terminal([(prompt, answer)], expected=1)
            self.assertEqual([path.read_bytes() for path in paths], edited)
        self.terminal([(prompt, b"s\n"), (prompt, b"o\n")], expected=1)
        self.assertEqual(paths[0].read_bytes(), edited[0])
        self.assertEqual(paths[1].read_bytes(), original[1])
        output = self.terminal([(prompt, b"x\n"), (prompt, b"d\n"), (prompt, b"o\n")])
        self.assertIn("diff --git", output)
        self.assertIn("Type one letter, then press Enter.", output)
        self.assert_clean()
        for path, content in zip(paths, edited):
            path.write_bytes(content)
        self.terminal([(prompt, b"a\n")])
        self.assertEqual([path.read_bytes() for path in paths], original)
        self.assert_clean()

    def test_unattended_conflict_keeps_local_edits(self):
        self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        self.complete_profile()
        path = self.home / ".aliases"
        path.write_bytes(path.read_bytes() + b"\n# local edit\n")
        before = path.read_bytes()
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            input="all-overwrite\n",
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Apply stopped", result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_an_unrelated_home_dotfiles_folder_stays_untouched(self):
        (self.home / "dotfiles").mkdir()
        marker = self.home / "dotfiles/keep"
        marker.write_text("keep")
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--defaults",
                "--repo",
                os.environ["DOTFLEET_CONTENT_FIXTURE"],
                "--domain",
                "core",
                "--git-name",
                "Fixture",
                "--git-email",
                "fixture@example.invalid",
            ],
            env=self.env,
            text=True,
            capture_output=True,
        )
        # Content now lives in ~/.fleet/dotfiles, so a folder of the user's own never blocks setup.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(sorted(path.name for path in (self.home / "dotfiles").iterdir()), ["keep"])
        self.assertTrue((dotfiles_library.active(self.home) / ".git").is_dir())


class ConverterTests(unittest.TestCase):
    def test_modes_furniture_and_manifest_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(
                root / "repo",
                {
                    ".zshrc": "hello\n",
                    "README.md": "furniture",
                    ".github/workflows/test.yml": "furniture",
                    "agents/skills/demo/SKILL.md": "skill",
                    "dotfleet.toml": 'format="plain"\npaths=["agents/skills"]\n',
                },
            )
            (repo / ".zshrc").chmod(0o755)
            (repo / ".link").symlink_to(".zshrc")
            command("git", "-C", str(repo), "add", ".")
            source, items, _ = convert(repo, root / "stage")
            self.assertEqual(set(items), {".zshrc", ".link", "agents/skills/demo/SKILL.md"})
            self.assertTrue((source / "executable_dot_zshrc").exists())
            self.assertEqual((source / "symlink_dot_link").read_text(), ".zshrc")
            self.assertFalse((source / "README.md").exists())

    def test_literal_names_and_empty_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(
                root / "repo",
                {
                    ".config/run_example": "literal",
                    ".config/example.tmpl": "{{ untouched }}",
                    ".empty": "",
                    ".config/literal_example": "literal",
                },
            )
            source, _, _ = convert(repo, root / "stage")
            home = root / "home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home))
            command(
                "chezmoi",
                "--source",
                str(source),
                "--destination",
                str(home),
                "--config",
                str(root / "missing.toml"),
                "apply",
                env=env,
            )
            for name in [
                ".config/run_example",
                ".config/example.tmpl",
                ".empty",
                ".config/literal_example",
            ]:
                self.assertEqual((home / name).read_bytes(), (repo / name).read_bytes())

    def test_domain_preview_lists_actual_commands_without_running_them(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = repository(
                Path(temp) / "content",
                {
                    ".config/dotfiles/aliases/personal.zsh": "# Placeholder\n\n",
                    ".config/dotfiles/aliases/work.zsh": "alias project='pwd'\nreview() { :; }\n# alias ignored='false'\n",
                },
            )
            # Untracked files must not appear as content that will be applied.
            (checkout / ".config/dotfiles/aliases/client.zsh").write_text(
                "alias untracked='false'\n"
            )
            detail = domain_details(checkout)
            self.assertIn("personal  Adds nothing yet", detail)
            self.assertIn("Command names: project, review.", detail)
            self.assertNotIn("ignored", detail)
            self.assertIn("client    Additions are not described here", detail)
            self.assertNotIn("No declared domain adds commands yet", detail)

    def test_full_versions_and_floating_selectors(self):
        for version in [
            "24.14.1",
            "v3.12.0",
            "3.13.0t",
            "21.0.2-tem",
            "temurin-21.0.2+13.0.LTS",
            "2026-11-16",
            "a" * 40,
        ]:
            self.assertTrue(exact_version(version), version)
        for version in [
            "latest",
            "lts",
            "stable",
            "nightly",
            "24",
            "3.13",
            "24.x",
            "^24.1.0",
            ">=24.1.0",
        ]:
            self.assertFalse(exact_version(version), version)

    def test_version_lists_and_options_keep_exact_pins(self):
        profile = {
            "domain": "core",
            "domain_chosen": True,
            "git_name": "Fixture",
            "git_email": "fixture@example.invalid",
            "pins": {"node": ["22.1.0", {"version": "latest", "option": "keep"}]},
        }
        with (
            patch("manager.resolve_installed_pin", return_value="24.14.1"),
            patch("manager.ask", return_value="latest"),
        ):
            warnings = validate_profile(Path("/tmp"), profile)
        self.assertEqual(warnings, [])
        self.assertEqual(
            profile["pins"], {"node": ["22.1.0", {"version": "24.14.1", "option": "keep"}]}
        )

    def test_defaults_only_fill_installed_gaps(self):
        with (
            patch("manager.run", return_value=json.dumps({"node": [{}], "python": [{}]})),
            patch("manager.resolve_installed_pin", return_value="3.13.0"),
        ):
            pins, available = installed_defaults(
                {"node": "24", "python": "3.13", "uv": "0.12"}, {"node": "22"}, Path("/tmp")
            )
        self.assertEqual(pins, {"node": "22", "python": "3.13.0"})
        self.assertEqual(available, {"uv": "0.12"})

    def test_native_manifest_all_platforms(self):
        manifest = tomllib.loads((ROOT / "agents/manifest.toml").read_text())
        self.assertEqual(
            set(manifest["agents"]),
            {"antigravity", "claude", "codex", "gemini", "grok", "pi", "opencode", "ollama"},
        )
        for agent in manifest["agents"].values():
            self.assertEqual(set(agent["config_paths"]), {"darwin", "linux", "windows", "wsl"})
            self.assertEqual(set(agent["skills_paths"]), {"darwin", "linux", "windows", "wsl"})

    def test_shell_help_and_unknown_flags(self):
        # A throwaway home: an invalid run may still migrate stores or record a failed attempt.
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = dict(os.environ, HOME=home.name)
        for path in [ROOT / "install.sh", *sorted((ROOT / "scripts").glob("*.sh"))]:
            for flag in ["--help", "-h"]:
                output = command("bash", str(path), flag, env=env)
                words = (
                    ["Usage:", "global options:"]
                    if path.name == "dotfleet.sh"
                    else ["Usage:", "Example"]
                )
                for word in words:
                    self.assertIn(word, output, str(path))
                self.assertNotIn("exit code", output.lower(), str(path))
            result = subprocess.run(
                ["bash", str(path), "--unknown"], text=True, capture_output=True, env=env
            )
            self.assertEqual(result.returncode, 2, str(path))
            self.assertIn("Usage:", result.stderr)

    def test_unknown_format_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = repository(Path(temp) / "repo", {"README.md": "nothing"})
            with self.assertRaisesRegex(ValueError, "Unknown content format"):
                detect(repo)


if __name__ == "__main__":
    unittest.main()
