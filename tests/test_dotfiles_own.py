"""Check fleet dotfiles own and fleet dotfiles source.

Own makes the active content your own repository: the old origin becomes upstream.
Source saves a new content address; the next update moves the checkout's origin to it.
Every remote is a local test folder. A fake gh and a fake ssh stand in for the network.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_dotfiles_own.py --help.
"""

import json
import subprocess
import tomllib
import unittest

import registry
import remote
import test_capture
import test_command
from test_manager import command

WHERE = b"Own repository [c]:"


class DotfilesOwnTests(unittest.TestCase):
    setUp = test_capture.CaptureTests.setUp
    terminal = test_capture.CaptureTests.terminal
    content_repository = test_capture.CaptureTests.content_repository
    manager_origin = test_capture.CaptureTests.manager_origin
    enroll = test_capture.CaptureTests.enroll
    home_env = test_capture.CaptureTests.home_env
    git = test_capture.CaptureTests.git

    def checkout(self):
        (checkout,) = (self.home / ".fleet/dotfiles").iterdir()
        return checkout

    def remotes(self):
        names = self.git(self.checkout(), "remote").split()
        return {
            name: self.git(self.checkout(), "remote", "get-url", name).strip() for name in names
        }

    def saved_source(self):
        return tomllib.loads((self.home / ".fleet/profile.toml").read_text())["content_repo"]

    def read_only_origin(self):
        """Serve the content over a fake ssh that allows fetches and refuses pushes, as a host
        does for someone else's repository. Members other than readonly are unreachable."""
        fake = self.base / "fake-ssh"
        fake.mkdir(exist_ok=True)
        (fake / "ssh").write_text(
            "#!/bin/sh\n"
            'for argument; do host="$command"; command="$argument"; done\n'
            'case "$host" in *readonly*) ;; *) echo "Connection refused" >&2; exit 255;; esac\n'
            'case "$command" in git-receive-pack*)\n'
            '  echo "ERROR: Permission to content denied." >&2; exit 128;; esac\n'
            'exec /bin/sh -c "$command"\n'
        )
        (fake / "ssh").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        address = f"ssh://readonly{self.bare}"
        self.git(self.checkout(), "remote", "set-url", "origin", address)
        return address

    def empty_repository(self, name="mine.git"):
        path = self.base / name
        command("git", "init", "-q", "--bare", "-b", "main", str(path))
        return path

    def test_u_makes_the_typed_repository_origin_and_keeps_the_old_one_as_upstream(self):
        self.content_repository()
        self.git(self.content, "tag", "v1")
        # The non-bare content repository refuses pushes, so it is not yours.
        self.git(self.content, "push", "-q", str(self.bare), "v1")
        self.enroll(self.bare)
        old = self.read_only_origin()
        mine = self.empty_repository()
        answers = [(WHERE, b"u\n"), (b"Address", f"{mine}\n".encode())]
        output = self.terminal(["dotfiles", "own"], answers)
        self.assertIn(f"origin is now {mine}.", output)
        self.assertEqual(self.remotes(), {"origin": str(mine), "upstream": old})
        self.assertEqual(self.git(mine, "tag").split(), ["v1"])
        self.assertEqual(
            self.git(mine, "rev-parse", "main").strip(),
            self.git(self.checkout(), "rev-parse", "HEAD").strip(),
        )
        self.assertEqual(self.saved_source(), str(mine))
        own = registry.load(self.home)["machines"][registry.machine_name(self.home)]
        self.assertEqual(own["content_repo"], str(mine))
        # The checkout still belongs to the saved source, so a check works.
        test_command.CommandTests.cli(self, "check", expected=0)

    def test_an_origin_you_can_push_to_is_already_yours(self):
        self.content_repository()
        self.enroll(self.bare)
        before = self.remotes()
        output = test_command.CommandTests.cli(self, "dotfiles", "own")
        self.assertIn("origin is already yours", output)
        self.assertEqual(self.remotes(), before)

    def test_g_creates_the_repository_with_gh_and_private_preselected(self):
        self.content_repository()
        self.enroll(self.bare)
        self.read_only_origin()
        fake = self.base / "fake-gh"
        fake.mkdir()
        record = self.base / "gh-arguments"
        (fake / "gh").write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >>"{record}"\n'
            '[ "$1 $2" = "auth status" ] && exit 0\n'
            'if [ "$1 $2" = "repo create" ]; then\n'
            f'  git init -q --bare -b main "{self.base}/$3.git" && echo "{self.base}/$3.git"\n'
            "  exit 0\nfi\nexit 1\n"
        )
        (fake / "gh").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        answers = [
            (WHERE, b"g\n"),
            (b"Repository name [", b"\n"),
            (b"Visibility [private]:", b"\n"),
        ]
        output = self.terminal(["dotfiles", "own"], answers)
        created = self.base / f"{self.checkout().name}.git"
        self.assertIn(f"origin is now {created}.", output)
        self.assertIn(f"repo create {self.checkout().name} --private", record.read_text())
        self.assertEqual(self.remotes()["origin"], str(created))

    def test_c_cancels_and_changes_nothing(self):
        self.content_repository()
        self.enroll(self.bare)
        self.read_only_origin()
        before = self.remotes()
        output = self.terminal(["dotfiles", "own"], [(WHERE, b"c\n")])
        self.assertIn("Cancelled. Nothing changed.", output)
        self.assertEqual(self.remotes(), before)
        self.assertEqual(self.saved_source(), str(self.bare))

    def test_a_push_that_fails_puts_the_remotes_back(self):
        self.content_repository()
        self.enroll(self.bare)
        self.read_only_origin()
        before = self.remotes()
        missing = self.base / "missing.git"
        answers = [(WHERE, b"u\n"), (b"Address", f"{missing}\n".encode())]
        output = self.terminal(["dotfiles", "own"], answers, expected=1)
        self.assertIn(f"Could not push to {missing}. Nothing changed.", output)
        self.assertEqual(self.remotes(), before)
        self.assertEqual(self.saved_source(), str(self.bare))

    def test_own_sends_the_source_to_members_and_names_the_rest(self):
        self.content_repository()
        self.enroll(self.bare)
        self.read_only_origin()
        data = registry.load(self.home)
        member = dict(registry.blank("lab"), machine_id="e" * 32, enrolled=True)
        member.update(ssh_target="lab-host")
        data["machines"]["lab"] = member
        (self.home / registry.REGISTRY).write_text(registry.serialize(data))
        mine = self.empty_repository()
        answers = [(WHERE, b"u\n"), (b"Address", f"{mine}\n".encode())]
        output = self.terminal(["dotfiles", "own"], answers)
        flowing = " ".join(output.split())
        self.assertIn(f"dotfiles source --repo {mine} --machine lab", flowing)

    def test_a_saved_source_moves_origin_at_the_next_update(self):
        self.content_repository()
        self.enroll(self.content)
        mine = self.empty_repository()
        self.git(self.checkout(), "push", "-q", str(mine), "HEAD:main")
        output = test_command.CommandTests.cli(self, "dotfiles", "source", "--repo", str(mine))
        self.assertIn("The next update moves this checkout's origin", output)
        test_command.CommandTests.cli(self, "update")
        self.assertEqual(self.remotes(), {"origin": str(mine), "upstream": str(self.content)})

    def test_unpushed_commits_keep_the_old_source(self):
        self.content_repository()
        self.enroll(self.content)
        mine = self.empty_repository()
        self.git(self.checkout(), "push", "-q", str(mine), "HEAD:main")
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        self.git(self.checkout(), *identity, "commit", "-q", "--allow-empty", "-m", "local")
        test_command.CommandTests.cli(self, "dotfiles", "source", "--repo", str(mine))
        result = subprocess.run(
            [str(self.exe), "update"], env=self.env, cwd=self.home, text=True, capture_output=True
        )
        output = result.stdout + result.stderr
        self.assertIn("Fleet keeps the old content source", output)
        self.assertIn("commits that are not pushed", output)
        self.assertEqual(self.remotes(), {"origin": str(self.content)})
        self.assertEqual(self.saved_source(), str(self.content))


class SourceRequestTests(unittest.TestCase):
    """The remote request carries only a repository address."""

    def test_an_address_is_accepted_and_shell_text_is_refused(self):
        for repo in ("https://github.com/me/dotfiles.git", "git@github.com:me/dotfiles.git"):
            request = remote.profile_request("dotfiles", "source", {"repo": repo})
            self.assertEqual(json.loads(json.dumps(request))["arguments"], {"repo": repo})
        for repo in ("x; rm -rf ~", "$(id)", ""):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                remote.profile_request("dotfiles", "source", {"repo": repo})


if __name__ == "__main__":
    unittest.main()
