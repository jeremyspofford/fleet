"""Exercise the installed command, including terminal confirmations and saved records.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_command.py --help.
"""

import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import shutil
import subprocess
import termios
import time
import tomllib
import unittest

import dotfiles_library

import test_manager as helpers
from test_manager import ROOT, command, repository
from common import toml


class CommandTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status

    def enroll(self):
        self.origin = self.base / "manager-origin"
        command("git", "clone", "--no-hardlinks", str(ROOT), str(self.origin))
        command("git", "-C", str(self.origin), "checkout", "-B", "main")
        for name in command("git", "-C", str(ROOT), "ls-files", "-z").split("\0"):
            if name and (ROOT / name).is_file():
                target = self.origin / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        command("git", "-C", str(self.origin), "add", ".")
        command(
            "git",
            "-C",
            str(self.origin),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "feat: command fixture",
        )
        command("git", "-C", str(self.origin), "remote", "set-url", "origin", str(self.origin))
        command("git", "-C", str(self.origin), "config", "branch.main.remote", "origin")
        command("git", "-C", str(self.origin), "config", "branch.main.merge", "refs/heads/main")
        self.content = repository(
            self.base / "content",
            {
                "dotfleet.toml": 'format="plain"\n[groups.extra]\ndescription="Extra file"\nfiles=[".extra"]\n[domains]\ncore="Base only"\npersonal="Personal shortcut"\n',
                ".file": "original\n",
                ".extra": "group\n",
                ".zshrc": 'source "$HOME/.aliases"\n',
                ".bashrc": 'source "$HOME/.aliases"\n',
                ".aliases": 'domain=$(cat "$HOME/.config/dotfiles/domain"); source "$HOME/.config/dotfiles/aliases/$domain.zsh"\n',
                ".config/dotfiles/aliases/core.zsh": 'alias chosen="echo core"\n',
                ".config/dotfiles/aliases/personal.zsh": 'alias chosen="echo personal"\n',
            },
        )
        (self.home / ".file").write_text("before enrollment\n")
        output = command(
            "bash",
            str(self.origin / "install.sh"),
            "--repo",
            str(self.content),
            "--defaults",
            "--domain",
            "core",
            "--git-name",
            "Command Example",
            "--git-email",
            "command@example.invalid",
            env=self.env,
        )
        self.exe = self.home / ".local/bin/dotfleet"
        self.assertTrue(self.exe.is_file())
        self.assertIn("dotfleet", output)
        self.env["PATH"] = str(self.home / ".local/bin") + ":" + self.env["PATH"]
        return output

    def cli(self, *args, expected=0):
        result = subprocess.run(
            [str(self.exe), *args], env=self.env, cwd=self.home, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def record(self):
        paths = sorted((self.home / ".fleet/backups").glob("*/RUN.json"))
        return json.loads(paths[-1].read_text()), paths[-1].parent

    def terminal(self, args, answers, expected=0):
        master, slave = pty.openpty()

        def setup():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            [str(self.exe), *args],
            env=self.env,
            cwd=self.home,
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=slave,
            preexec_fn=setup,
        )
        os.close(slave)
        process.stdin.close()
        data, cursor, sent = b"", 0, 0
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    data += chunk
                if sent < len(answers) and answers[sent][0] in re.sub(
                    rb"\x1b\[[0-9;]*m", b"", data[cursor:]
                ):
                    os.write(master, answers[sent][1])
                    sent += 1
                    cursor = len(data)
                if process.poll() is not None:
                    break
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        finally:
            os.close(master)
        text = re.sub(r"\x1b\[[0-9;]*m", "", data.decode()).replace("\r\n", "\n")
        self.assertEqual(process.returncode, expected, text)
        self.assertEqual(sent, len(answers), text)
        return text

    def assert_review_golden(self, name, output):
        from render_run import render

        record, _ = self.record()
        self.assertTrue(output.rstrip().endswith(render(record).rstrip()), output)
        # Compare stable example values; releases and host tool versions must not break snapshots.
        data = json.dumps(record).replace(str(self.content), "https://example.invalid/dotfiles")
        data = data.replace(str(self.home), "/home/example")
        data = re.sub(
            r"/.fleet/backups/\d{8}-\d{6}(?:-\d+)?", "/.fleet/backups/20261117-120000", data
        )
        record = json.loads(data)
        for tool in record["software"]:
            if tool["name"] in ("chezmoi", "Mise", "dotfleet", "Fleet", "Fleet TUI (Textual)"):
                tool["version"] = "0.1.0"
                if tool.get("old"):
                    tool["old"] = "0.0.1"
        for checkout in record["checkouts"]:
            unchanged = checkout["old"] == checkout["new"]
            if checkout["old"]:
                checkout["old"] = "1" * 40
            if checkout["new"]:
                checkout["new"] = "1" * 40 if unchanged else "2" * 40
        self.assert_golden(name, render(record))

    def assert_golden(self, name, text):
        self.assertEqual(text, (ROOT / f"tests/fixtures/views/{name} review.txt").read_text())

    def test_all_help_and_invalid_arguments(self):
        from command import DESCRIPTIONS

        for name in [None, *DESCRIPTIONS]:
            args = [name, "--help"] if name else ["--help"]
            output = command("bash", str(ROOT / "scripts/dotfleet.sh"), *args, env=self.env)
            self.assertIn("help", output)
            self.assertNotIn("Usage: Usage:", output)
            for line in output.splitlines():
                self.assertLessEqual(len(line), 100)
            # Help never shows exit codes; commands still return them.
            self.assertNotIn("exit code", output.lower())
            if name and name != "help":
                self.assertIn(DESCRIPTIONS[name].split(".")[0].split()[0], output)
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/dotfleet.sh"), "--unknown"],
            env=self.env,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_update_status_diff_and_version(self):
        self.enroll()
        # A real manager change is fetched from a local remote and reported by commit.
        (self.origin / "README.md").write_text(
            (self.origin / "README.md").read_text() + "\nFixture update.\n"
        )
        command("git", "-C", str(self.origin), "add", ".")
        command(
            "git",
            "-C",
            str(self.origin),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "docs: fixture update",
        )
        before = (self.home / ".file").read_bytes()
        preview = self.cli("diff")
        self.assertTrue(preview.startswith("No differences. Your files match "))
        self.assertEqual(len(preview.splitlines()), 1)
        self.assertEqual((self.home / ".file").read_bytes(), before)
        output = self.cli("update")
        self.assertIn("2 · Manager and content — 1 updated.", output)
        self.assert_review_golden("update", output)
        self.assertTrue(self.record()[0]["completed_checkouts"])
        self.assert_quiet_status()
        status = self.cli("status")
        self.assertIn("core · chosen", status)
        self.assertIn("0 behind last fetched upstream", status)
        Path("/tmp/dotfleet-command-status.txt").write_text(status)
        self.assertIn("Content commit", self.cli("version"))
        self.assertIn("needs a terminal", self.cli("configure", expected=1))
        self.assertIn("No backup taken.", self.cli("update"))

    def test_configure_keeps_the_terminal_wizard_and_standard_review(self):
        self.enroll()
        output = self.terminal(
            ["configure"],
            [
                (b"Content source [repo]:", b"\n"),
                (b"Content repository [", b"\n"),
                (b"Domain [core]:", b"\n"),
                (b"Git author name [", b"\n"),
                (b"Git author email [", b"\n"),
                (b"Tools [accept]:", b"\n"),
                (b"extra [on]:", b"\n"),
                (b"Review [confirm]:", b"confirm\n"),
            ],
        )
        self.assertIn("1. Welcome", output)
        self.assertIn("9. Review", output)
        self.assert_review_golden("configure", output)
        self.assert_quiet_status()

    def test_domain_and_groups_use_saved_profile(self):
        self.enroll()
        self.assertIn("Personal shortcut", self.cli("domain", "list"))
        self.assert_review_golden("domain", self.cli("domain", "set", "--name", "personal"))
        p = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(p["domain"], "personal")
        self.assertTrue(p["domain_chosen"])
        output = command("zsh", "-ic", "chosen", env=self.env, cwd=self.home)
        self.assertIn("personal", output)
        self.assert_review_golden(
            "groups", self.cli("groups", "set", "--name", "extra", "--state", "off")
        )
        self.assertFalse((self.home / ".extra").exists())
        self.assertIn("− ~/.extra", self.record_text())
        self.cli("groups", "set", "--name", "extra", "--state", "on")
        self.assertEqual((self.home / ".extra").read_text(), "group\n")
        self.assert_quiet_status()

    def record_text(self):
        from render_run import render

        return render(self.record()[0])

    def test_backup_rollback_and_confirmation(self):
        self.enroll()
        self.assert_review_golden("backup", self.cli("backups", "create"))
        _, saved = self.record()
        original_profile = (self.home / ".fleet/profile.toml").read_bytes()
        (self.home / ".file").write_text("local edit\n")
        output = self.cli("backups", "restore", "--backup", saved.name, expected=1)
        self.assertIn("needs a terminal", output)
        self.assertEqual((self.home / ".file").read_text(), "local edit\n")
        output = self.terminal(
            ["backups", "restore", "--backup", saved.name], [(b"Confirm [cancel]:", b"restore\n")]
        )
        self.assertIn("~ ~/.file", output)
        self.assert_review_golden("rollback", output)
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), original_profile)
        self.assert_quiet_status()

    def test_doctor_names_all_gaps(self):
        self.enroll()
        self.assertNotIn("FAIL", self.cli("doctor"))
        path = self.home / ".fleet/profile.toml"
        p = tomllib.loads(path.read_text())
        p.update(domain_chosen=False, git_name="", git_email="", pins={"node": "latest"})
        path.write_text(toml(p))
        output = self.cli("doctor", expected=1)
        for text in [
            "never explicitly chosen",
            "Git name is empty",
            "Git email is empty",
            "node tool version is floating",
        ]:
            self.assertIn(text, output)
        for fix in [
            "dotfleet domain",
            "dotfleet configure",
            "dotfleet tools set node --version VERSION",
        ]:
            self.assertIn(fix, output)
        self.assertNotIn("Supply --", output)

    def test_missing_domain_provenance_is_unchosen_until_named_choice(self):
        self.enroll()
        path = self.home / ".fleet/profile.toml"
        p = tomllib.loads(path.read_text())
        del p["domain_chosen"]
        path.write_text(toml(p))
        before = path.read_bytes()
        output = self.cli("status")
        self.assertIn("core · UNCHOSEN", output)
        self.assertIn("dotfleet domain", output)
        output = self.cli("doctor", expected=1)
        self.assertIn("FAIL · UNCHOSEN", output)
        self.assertIn("never explicitly chosen", output)
        self.assertIn("dotfleet domain set --name NAME", " ".join(output.split()))
        self.assertNotIn("Supply --domain", output)
        choices = self.cli("domain", "list")
        self.assertIn("UNCHOSEN", choices)
        self.assertIn("Personal shortcut", choices)
        self.assertIn("dotfleet domain set --name NAME", choices)
        self.assertEqual(path.read_bytes(), before)
        self.cli("domain", "set", "--name", "core")
        self.assertTrue(tomllib.loads(path.read_text())["domain_chosen"])
        self.assertIn("core · chosen", self.cli("status"))
        self.assertNotIn("FAIL", self.cli("doctor"))
        self.assert_quiet_status()

    def test_explicit_pin_installs_exact_version_and_remove_blocks_defaults(self):
        self.enroll()
        mise = self.home / ".local/bin/mise"
        mise.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
home=Path.home(); store=home/'.pin-test.json'; args=sys.argv[1:]
items=json.loads(store.read_text()) if store.exists() else {}
if args[0]=='--version': print('2026.8.5')
elif args[0]=='ls': print(json.dumps(items))
elif args[0]=='install':
    tool,version=args[1].rsplit('@',1)
    path=home/'.local/share/mise/installs'/tool/version
    path.mkdir(parents=True,exist_ok=True)
    items[tool]=[{'version':version,'install_path':str(path)}]
    store.write_text(json.dumps(items))
elif args[0]=='latest':
    tool,version=args[-1].rsplit('@',1)
    if any(x['version']==version for x in items.get(tool,[])): print(version)
    else: sys.exit(1)
else: sys.exit(1)
""")
        mise.chmod(0o700)
        output = self.cli("tools", "set", "node", "--version", "24.14.1")
        self.assertIn("24.14.1", output)
        self.assert_review_golden("pins", output)
        record, _ = self.record()
        self.assertIn("node", record["completed_software"])
        p = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(p["pins"]["node"], "24.14.1")
        self.assertIn("  node  24.14.1  installed\n", self.cli("tools", "list"))
        self.cli("tools", "remove", "node")
        p = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertNotIn("node", p["pins"])
        self.assertIn("node", p["unpinned_tools"])
        self.assertNotIn(
            "node",
            tomllib.loads((self.home / ".config/mise/conf.d/fleet.toml").read_text())["tools"],
        )
        self.assertTrue((self.home / ".local/share/mise/installs/node/24.14.1").exists())
        self.cli("tools", "set", "node", "--version", "latest", expected=2)
        self.assert_quiet_status()

    def test_external_restore_backs_up_the_current_target(self):
        self.enroll()
        external = self.base / "external"
        external.write_text("saved external\n")
        path = self.home / ".file"
        path.unlink()
        path.symlink_to(external)
        self.cli("backups", "create")
        _, saved = self.record()
        external.write_text("current external\n")
        self.terminal(
            ["backups", "restore", "--backup", saved.name], [(b"Confirm [cancel]:", b"restore\n")]
        )
        _, newest = self.record()
        self.assertEqual(external.read_text(), "saved external\n")
        self.assertEqual(
            (newest / "external" / str(external).lstrip("/")).read_text(), "current external\n"
        )
        path.unlink()
        path.write_text("regular file now\n")
        external.write_text("untracked external change\n")
        self.assertIn(
            "original link is missing",
            self.cli("backups", "restore", "--backup", saved.name, expected=1),
        )
        self.assertEqual(external.read_text(), "untracked external change\n")

    def test_rollback_rejects_changed_backup_and_unsafe_paths(self):
        self.enroll()
        self.cli("backups", "create")
        _, saved = self.record()
        (saved / "home/.file").write_text("tampered\n")
        output = self.cli("backups", "restore", "--backup", saved.name, expected=1)
        self.assertIn("failed its recorded fingerprint", output)
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        self.assertIn(
            "directly inside", self.cli("backups", "restore", "--backup", "/tmp", expected=1)
        )

    def test_uninstall_keeps_content_and_backups_and_can_restore_oldest(self):
        self.enroll()
        self.cli("uninstall", expected=1)
        cancelled = self.terminal(
            ["uninstall"],
            [(b"Oldest backup [keep]:", b"keep\n"), (b"Confirm [cancel]:", b"cancel\n")],
        )
        self.assertIn("cancelled", cancelled)
        self.assertTrue(self.exe.exists())
        output = self.terminal(
            ["uninstall"],
            [(b"Oldest backup [keep]:", b"restore\n"), (b"Confirm [cancel]:", b"uninstall\n")],
        )
        self.assertIn("− ~/.local/bin/dotfleet", output)
        self.assert_review_golden("uninstall", output)
        self.assertFalse(self.exe.exists())
        self.assertFalse((self.home / ".fleet/manager").exists())
        self.assertTrue((self.home / ".fleet/dotfiles/content/.git").exists())
        self.assertTrue((self.home / ".fleet/backups").exists())
        self.assertEqual((self.home / ".file").read_text(), "before enrollment\n")


if __name__ == "__main__":
    unittest.main()
