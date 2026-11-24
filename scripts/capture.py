"""Capture edited home files into content, commit them, and push to your own origin.

Internal API: run(home, names, display, update).
To capture means to copy an edit of a managed home file back into content.
This first version captures whole files from plain and Stow content, for every machine.
It pushes only to an origin that you can push to. Otherwise the commit stays local.
Use dotfleet capture --help for the user-facing command.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.

Usage: import this module from Fleet.
Help: python3 scripts/capture.py --help.
"""

import difflib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import convert
import dotfiles_library
import enrollment
import manager
import registry
from common import read_toml

PROFILE = ".fleet/profile.toml"
SOURCE = ".fleet/chezmoi-source"
MARKER = re.compile(rb"^# dotfleet:group \S+ (?:begin|end)$", re.M)
LIMIT = 2 * 1024 * 1024
ANSWER = "capture"


class Refused(ValueError):
    """One file cannot be captured. Nothing changes for it."""


def git(checkout, *arguments, check=True):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        capture_output=True,
        check=check,
        env=environment,
    )


def merge(ours, base, theirs):
    """Merge THEIRS into OURS with BASE as the common ancestor. Return the result, or None."""
    with tempfile.TemporaryDirectory(prefix="fleet-capture-") as folder:
        paths = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(folder) / name
            path.write_bytes(data)
            paths.append(str(path))
        result = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def restore_blocks(content, rendered, edited):
    """Replay the edit from RENDERED to EDITED on CONTENT, which also holds omitted blocks.

    RENDERED is CONTENT without the blocks of this machine's disabled groups, so each rendered
    line maps to one content line. An insertion goes right after its preceding kept line.
    Return None when an edit spans an omitted block or the files do not match that way.
    """
    whole, shown, new = (data.splitlines(keepends=True) for data in (content, rendered, edited))
    where, matched = {}, 0
    for block in difflib.SequenceMatcher(a=whole, b=shown, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            where[block.b + offset] = block.a + offset
        matched += block.size
    if matched != len(shown):
        return None
    result = list(whole)
    edits = difflib.SequenceMatcher(a=shown, b=new, autojunk=False).get_opcodes()
    for tag, start, end, first, last in reversed(edits):
        if tag == "equal":
            continue
        if start == end:
            position = where[start - 1] + 1 if start else 0
            result[position:position] = new[first:last]
            continue
        low, high = where[start], where[end - 1] + 1
        if high - low != end - start:
            return None  # the changed lines surround an omitted block
        result[low:high] = new[first:last]
    return b"".join(result)


def destination(home, name):
    """Return NAME as a path relative to HOME. A relative NAME is relative to HOME."""
    path = Path(name).expanduser()
    path = path if path.is_absolute() else home / path
    try:
        return path.relative_to(home).as_posix()
    except ValueError:
        raise Refused(f"{name} is outside your home folder.") from None


def added_lines(before, after):
    """Yield (line number in AFTER, line) for each line that AFTER adds to BEFORE."""
    old, new = before.splitlines(), after.splitlines()
    for tag, _, _, start, end in difflib.SequenceMatcher(a=old, b=new).get_opcodes():
        if tag in ("insert", "replace"):
            for number in range(start, end):
                yield number + 1, new[number]


def prepare(home, checkout, items, installed, name):
    """Return (content name, content now, captured content) for one home file, or raise Refused."""
    target = destination(home, name)
    shown = "~/" + target
    if target not in items:
        raise Refused(f"{shown} is not a file that Fleet manages from this content.")
    content_name, mode = items[target]
    path = home / target
    if enrollment.PRIVATE_NAMES.search(target):
        raise Refused(f"{shown} has a credential file name. Fleet never captures it.")
    if mode == "120000" or path.is_symlink() or not path.is_file():
        raise Refused(f"{shown} is not a regular file. Capture takes regular files only.")
    if path.stat().st_size > LIMIT:
        raise Refused(f"{shown} is larger than the 2 MiB limit.")
    current = path.read_bytes()
    if b"\0" in current:
        raise Refused(f"{shown} holds binary data. Capture takes text files only.")
    rendered_path = home / SOURCE / convert.source_name(target, mode)
    if not rendered_path.exists():
        rendered_path = rendered_path.with_name("empty_" + rendered_path.name)
    rendered = rendered_path.read_bytes() if rendered_path.exists() else b""
    if current == rendered:
        raise Refused(f"{shown} has no edits to capture.")
    if MARKER.findall(rendered) != MARKER.findall(current):
        raise Refused(f"{shown} changes a group marker line. Edit group blocks in content.")
    for number, line in added_lines(rendered, current):
        if enrollment.SECRET.search(line):
            raise Refused(
                f"{shown} line {number} looks like a secret. Nothing was captured. "
                "Use a [secrets] reference in fleet.toml and a ~/.secrets variable instead."
            )
    base = git(checkout, "show", f"{installed}:{content_name}").stdout
    # Put the edit back into the full content file, with the blocks this machine omits.
    captured = restore_blocks(base, rendered, current)
    if captured is None:
        raise Refused(f"{shown} cannot be placed into the content file. Nothing changed for it.")
    now = git(checkout, "show", f"HEAD:{content_name}").stdout
    if now != base:
        captured = merge(now, base, captured)
        if captured is None:
            raise Refused(f"{shown} conflicts with newer content. Nothing changed for it.")
    return content_name, now, captured


def installed_commit(home, checkout):
    """Return the content commit that this machine last applied, else the checkout's HEAD."""
    try:
        record = registry.load(home)["machines"].get(registry.machine_name(home), {})
        commit = record.get("content_commit", "")
        if (
            commit
            and git(checkout, "cat-file", "-e", commit + "^{commit}", check=False).returncode == 0
        ):
            return commit
    except (OSError, ValueError):
        pass
    return git(checkout, "rev-parse", "HEAD").stdout.decode().strip()


def ask(display):
    """Ask on the terminal whether to capture. Return True for capture, False for cancel."""
    with open("/dev/tty") as reader, open("/dev/tty", "w") as writer:
        while True:
            writer.write(
                f"Type {ANSWER}, then press Enter, to commit these files. "
                "Press Enter alone to cancel.\n> "
            )
            writer.flush()
            answer = reader.readline()
            if not answer or answer.strip() == "":
                return False
            if answer.strip() == ANSWER:
                return True
            writer.write(f"Type {ANSWER} exactly, or press Enter alone to cancel.\n")


def ask_file():
    """Ask on the terminal for one file to capture. Return it, or "" to cancel."""
    with open("/dev/tty") as reader, open("/dev/tty", "w") as writer:
        writer.write(
            "File to capture, such as ~/.zshrc. Type the path, then press Enter. "
            "Press Enter alone to cancel.\n> "
        )
        writer.flush()
        return reader.readline().strip()


def run(home, names, display, update):
    """Capture NAMES into the active content. Return an exit code."""
    profile = read_toml(home / PROFILE)
    checkout = dotfiles_library.active(home, profile)
    kind, manifest = convert.detect(checkout)
    if kind == "chezmoi":
        raise ValueError(
            "Capture takes plain and Stow content only. "
            "For native chezmoi content, run chezmoi re-add FILE."
        )
    if git(checkout, "status", "--porcelain").stdout.strip():
        raise ValueError(
            f"The content checkout has uncommitted edits: {checkout}. Commit or discard them first."
        )
    installed = installed_commit(home, checkout)
    # Fetch content. When the checkout is behind, fast-forward the checkout only.
    if git(checkout, "fetch", "-q", "origin", check=False).returncode == 0:
        git(checkout, "merge", "-q", "--ff-only", "@{upstream}", check=False)
    items = convert.selected_paths(checkout, manifest, profile.get("groups", {}))
    plans, refused = [], []
    for name in names:
        try:
            plans.append((name, *prepare(home, checkout, items, installed, name)))
        except Refused as error:
            refused.append(str(error))
    for message in refused:
        print(f"Not captured: {message}")
    if not plans:
        return 1
    for _, content_name, now, captured in plans:
        diff = difflib.unified_diff(
            now.decode(errors="replace").splitlines(keepends=True),
            captured.decode(errors="replace").splitlines(keepends=True),
            f"content/{content_name}",
            f"content/{content_name} (captured)",
        )
        print("".join(diff), end="")
    print("The secret check cannot prove that no secret remains. Review every added line.")
    if not manager.has_terminal():
        raise ValueError(
            f"Capture needs a terminal. Run {display} capture in an interactive shell."
        )
    if not ask(display):
        print("Cancelled. Nothing changed.")
        return 1 if refused else 0
    for _, content_name, _, captured in plans:
        target = checkout / content_name
        mode = target.stat().st_mode & 0o777
        target.write_bytes(captured)
        target.chmod(mode)
    shown = ", ".join(destination(home, name) for name, *_ in plans)
    machine = registry.machine_name(home)
    identity = ["-c", f"user.name={profile.get('git_name') or 'Fleet'}"]
    identity += ["-c", f"user.email={profile.get('git_email') or 'fleet@localhost'}"]
    git(checkout, "add", "--", *(content_name for _, content_name, _, _ in plans))
    git(checkout, *identity, "commit", "-q", "-m", f"capture: {shown} from {machine}")
    print(f"Committed: capture: {shown} from {machine}")
    # Push only to an origin you can push to. Never wait for a password.
    if (
        git(checkout, "push", "--dry-run", "origin", "HEAD", check=False).returncode == 0
        and git(checkout, "push", "-q", "origin", "HEAD", check=False).returncode == 0
    ):
        print("Pushed to origin.")
    else:
        print("origin is not a repository you can push to.")
        print(f"The commit stays in {checkout}.")
        print(
            "Push it to a repository you own. A later Fleet release adds "
            f"{display} dotfiles own for this."
        )
    code = update()
    return 1 if refused else code


def usage():
    import argparse

    from style import ArgumentParser

    argument_parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
