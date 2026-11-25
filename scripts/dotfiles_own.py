"""Make the active content your own repository, and move content to a new address.

Internal API: own(home, display, ask, save_source, send); source(home, repo, display, save);
repoint(home, profile, display, save).
Own checks origin first. When you can push there, it is already yours.
Otherwise you choose: g creates a repository with gh, u takes an address you type, c cancels.
The old origin becomes upstream, and every branch and tag goes to the new origin.
Each reachable member then gets the new address; its next update moves its origin there.
Use dotfleet dotfiles own --help for the user-facing command.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.

Usage: import this module from Fleet.
Help: python3 scripts/dotfiles_own.py --help.
"""

# Exit codes: 0 when the content is yours, was already yours, or the choice was cancelled.
#   1 when the push to the new repository fails; nothing changes then.
import os
from pathlib import Path
import shutil
import subprocess

import dotfiles_library
import registry
import updates


def git(checkout, *arguments, check=True):
    """Run git in CHECKOUT without ever waiting for a password or a host key."""
    ssh = os.environ.get("GIT_SSH_COMMAND", "ssh") + " -o BatchMode=yes"
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        capture_output=True,
        text=True,
        check=check,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=ssh),
    )


def remote_url(checkout, name):
    found = git(checkout, "remote", "get-url", name, check=False)
    return found.stdout.strip() if found.returncode == 0 else ""


def already_own(checkout):
    """Return True when origin exists and takes a push from this checkout."""
    if not remote_url(checkout, "origin"):
        return False
    return git(checkout, "push", "--dry-run", "origin", "HEAD", check=False).returncode == 0


def gh_ready():
    """Return True when gh is installed and signed in."""
    if not shutil.which("gh"):
        return False
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def create_with_gh(name, visibility):
    """Create an empty repository with gh. Return its address, or "" on failure."""
    created = subprocess.run(
        ["gh", "repo", "create", name, f"--{visibility}"], capture_output=True, text=True
    )
    lines = created.stdout.strip().splitlines()
    return lines[-1].strip() if created.returncode == 0 and lines else ""


def move_to(checkout, address):
    """Make ADDRESS origin, keep the old origin as upstream, and push everything.

    Return True on success. On a failed push, put the remotes back and return False.
    """
    old = remote_url(checkout, "origin")
    if old:
        git(checkout, "remote", "rename", "origin", "upstream")
    git(checkout, "remote", "add", "origin", address)
    pushed = (
        git(checkout, "push", "-q", "origin", "--all", check=False).returncode == 0
        and git(checkout, "push", "-q", "origin", "--tags", check=False).returncode == 0
    )
    if pushed:
        # The branch now follows the new origin, so updates pull from it.
        branch = git(checkout, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
        if branch:
            git(checkout, "branch", "-q", "--set-upstream-to", f"origin/{branch}", check=False)
        return True
    git(checkout, "remote", "remove", "origin")
    if old:
        git(checkout, "remote", "rename", "upstream", "origin")
    return False


def members(home):
    """Return the names of other enrolled members that this machine can reach by SSH."""
    own = registry.machine_name(home)
    machines = registry.load(home)["machines"]
    return sorted(
        name
        for name, record in machines.items()
        if name != own and record.get("enrolled") and record.get("ssh_target")
    )


def own(home, display, ask, save_source, send):
    """Make the active content your own repository. Return an exit code.

    ASK(label, default, choices, detail) reads the terminal. SAVE_SOURCE(repo) saves this
    machine's content address. SEND(member, repo) asks one member to save it; it returns 0
    when the member saved it.
    """
    checkout = dotfiles_library.active(home)
    if already_own(checkout):
        print(f"origin is already yours: {remote_url(checkout, 'origin')}. Nothing changed.")
        return 0
    choices = [("u", "Type the address of an empty repository that you created.")]
    if gh_ready():
        choices.insert(0, ("g", "Create a repository with gh."))
    choices.append(("c", "Cancel. Nothing changes."))
    detail = "\n".join(f"  {key}  {effect}" for key, effect in choices)
    detail = (
        "Your own repository keeps this content's whole history.\n"
        f"{detail}\nType one letter, then press Enter. Press Enter alone to choose c."
    )
    choice = ask("Own repository", "c", [key for key, _ in choices], detail)
    if choice == "c":
        print("Cancelled. Nothing changed.")
        return 0
    if choice == "g":
        name = ask("Repository name", checkout.name, None, "The name of the new repository.")
        visibility = ask(
            "Visibility",
            "private",
            ["private", "public"],
            "  private  Only you can see it. Recommended: old history may hold secrets.\n"
            "  public   Anyone can see it.\n"
            "Press Enter alone to choose private.",
        )
        address = create_with_gh(name, visibility)
        if not address:
            print("gh could not create the repository. Nothing changed.")
            return 1
    else:
        address = ask("Address", "", None, "The address of an empty repository, such as a Git URL.")
        if not address:
            print("Cancelled. Nothing changed.")
            return 0
    if not move_to(checkout, address):
        print(f"Could not push to {address}. Nothing changed.")
        return 1
    print(f"origin is now {address}. The old origin is now upstream.")
    save_source(address)
    registry.record_content_source(home, address)
    missed = [name for name in members(home) if send(name, address) != 0]
    for name in missed:
        print(
            f"Could not reach {name}. Run {display} dotfiles source --repo {address} --machine {name}"
        )
    return 0


def repoint(home, profile, display, save):
    """Move the checkout's origin to the saved content address, keeping the old one as upstream.

    A checkout with unpushed commits, or with uncommitted edits to managed files, keeps its old
    source. Fleet then saves the old address back with SAVE(repo) and says why.
    """
    checkout = dotfiles_library.active(home, profile)
    wanted = profile.get("content_repo", "")
    current = remote_url(checkout, "origin")
    if not wanted or not current or wanted in (current, str(checkout)):
        return
    reason = ""
    upstream = git(checkout, "rev-parse", "-q", "--verify", "@{upstream}", check=False)
    if upstream.returncode == 0:
        ahead = git(checkout, "rev-list", "--count", "@{upstream}..HEAD").stdout.strip()
        if ahead != "0":
            reason = "its checkout has commits that are not pushed"
    if not reason and updates.managed_edits(checkout):
        reason = "its checkout has uncommitted edits to managed files"
    if reason:
        print(f"Fleet keeps the old content source, because {reason}.")
        print(f"Push or commit them, then run {display} dotfiles source --repo {wanted} again.")
        save(current)
        return
    if remote_url(checkout, "upstream"):
        git(checkout, "remote", "set-url", "origin", wanted)
    else:
        git(checkout, "remote", "rename", "origin", "upstream")
        git(checkout, "remote", "add", "origin", wanted)
    git(checkout, "fetch", "-q", "origin", check=False)
    branch = git(checkout, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
    if branch:
        git(checkout, "branch", "-q", "--set-upstream-to", f"origin/{branch}", check=False)
    print(f"Content source: origin is now {wanted}. The old origin is now upstream.")


def usage():
    import argparse

    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
