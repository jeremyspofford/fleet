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

# Exit codes: 0 when captured and updated, or cancelled; 1 when any file is refused or none
#   is left to capture; otherwise the update's own code.
import collections
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
import scan_view
from common import read_toml

PROFILE = ".fleet/profile.toml"
SOURCE = ".fleet/chezmoi-source"
MARKER = re.compile(rb"^# dotfleet:group \S+ (?:begin|end)$", re.M)
LIMIT = 2 * 1024 * 1024
ANSWER = "capture"


class Refused(ValueError):
    """One file cannot be captured. Nothing changes for it."""


# One file to capture: its home path, content file, the content now, and the captured content.
Plan = collections.namedtuple("Plan", "target content_name newest captured")
# Push errors that mean origin did not answer, as opposed to refusing the push.
UNREACHABLE = (
    "could not read from remote",
    "does not appear to be a git repository",
    "unable to access",
    "could not resolve",
    "connection refused",
    "connection timed out",
    "network is unreachable",
)


def git(checkout, *arguments, check=True):
    """Run git in CHECKOUT. Never wait for a password, a passphrase, or a host key."""
    ssh = os.environ.get("GIT_SSH_COMMAND", "ssh") + " -o BatchMode=yes"
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=ssh)
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


def omitted_ranges(content, name, manifest, groups):
    """Return {group: (first line, line after last)} for each block that this machine omits."""
    lines = content.splitlines()
    ranges = {}
    for group, spec in manifest.get("groups", {}).items():
        if name not in spec.get("blocks", []) or groups.get(group, True):
            continue
        begin = f"# dotfleet:group {group} begin".encode()
        end = f"# dotfleet:group {group} end".encode()
        if lines.count(begin) == 1 and lines.count(end) == 1:
            ranges[group] = (lines.index(begin), lines.index(end) + 1)
    return ranges


def restore_blocks(content, rendered, edited, omitted):
    """Replay the edit from RENDERED to EDITED on CONTENT, which also holds omitted blocks.

    OMITTED maps each group this machine turns off to its line range in CONTENT.
    RENDERED is CONTENT without those ranges, so each rendered line maps to one content line.
    An insertion goes right after its preceding kept line. Every line but the last ends with
    a newline, so a group marker always stays on its own line.
    Return the result, or raise Refused naming a group whose block the edit surrounds.
    """
    whole, shown, new = (data.splitlines(keepends=True) for data in (content, rendered, edited))
    hidden = {line for first, last in omitted.values() for line in range(first, last)}
    where = [index for index in range(len(whole)) if index not in hidden]
    if [whole[index] for index in where] != shown:
        raise Refused("does not match its content file")
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
        for group, (begin, finish) in omitted.items():
            if low < begin and finish <= high:
                raise Refused(f"changes lines around the disabled group {group}")
        result[low:high] = new[first:last]
    lines = [
        line if line.endswith(b"\n") or index == len(result) - 1 else line + b"\n"
        for index, line in enumerate(result)
    ]
    return b"".join(lines)


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


def prepare(home, checkout, items, installed, newest, name, manifest, groups):
    """Return (content name, newest content, captured content) for one home file.

    INSTALLED is the content commit this machine applied; NEWEST is the commit to capture onto.
    Raise Refused when the file cannot be captured.
    """
    target = destination(home, name)
    shown = "~/" + target
    if target not in items:
        raise Refused(f"{shown} is not a file that Fleet manages from this content.")
    content_name, mode = items[target]
    path = home / target
    if enrollment.PRIVATE_NAMES.search(target) or scan_view.sensitive(target):
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
    try:
        omitted = omitted_ranges(base, target, manifest, groups)
        captured = restore_blocks(base, rendered, current, omitted)
    except Refused as error:
        raise Refused(f"{shown} {error}. Nothing changed for it.") from None
    now = git(checkout, "show", f"{newest}:{content_name}").stdout
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


def ask_file():
    """Ask on the terminal for one file to capture. Return it, or "" to cancel."""
    with open("/dev/tty") as reader, open("/dev/tty", "w") as writer:
        writer.write(
            "File to capture, such as ~/.zshrc. Type the path, then press Enter. "
            "Press Enter alone to cancel.\n> "
        )
        writer.flush()
        return reader.readline().strip()


def newest_commit(checkout):
    """Fetch origin without moving the checkout. Return the commit to capture onto.

    That is origin's branch when the checkout is behind it, else the checkout's HEAD.
    """
    head = git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    fetched = git(checkout, "fetch", "-q", "origin", check=False)
    if fetched.returncode != 0:
        print("Could not fetch content from origin. Capture uses the checkout as it is.")
        return head
    upstream = git(checkout, "rev-parse", "-q", "--verify", "@{upstream}", check=False)
    newest = upstream.stdout.decode().strip()
    behind = (
        newest
        and git(checkout, "merge-base", "--is-ancestor", "HEAD", newest, check=False).returncode
        == 0
    )
    return newest if behind else head


def push(checkout, display):
    """Push HEAD to origin when you can push there. Say what happened otherwise."""
    result = git(checkout, "push", "--dry-run", "origin", "HEAD", check=False)
    if result.returncode == 0:
        result = git(checkout, "push", "-q", "origin", "HEAD", check=False)
    if result.returncode == 0:
        print("Pushed to origin.")
        return True
    reason = result.stderr.decode(errors="replace").lower()
    if any(text in reason for text in UNREACHABLE):
        print(f"origin could not be reached. The commit stays in {checkout}.")
        print(f"Run git -C {checkout} push origin HEAD when origin answers.")
        return False
    elif "fetch first" in reason or "non-fast-forward" in reason:
        print(f"origin has newer commits. The commit stays in {checkout}.")
        print(f"Run {display} update, then capture again.")
    else:
        print("origin is not a repository you can push to.")
        print(f"The commit stays in {checkout}.")
        print(
            "Push it to a repository you own. A later Fleet release adds "
            f"{display} dotfiles own for this."
        )
    return True


def end_with_newline(home, plans, manifest, groups):
    """Add the final newline that a captured file needs before a group marker.

    Content keeps each marker on its own line, so the new rendering can end with a newline
    that the home file lacks. Without it, the update would ask about a one-byte difference.
    """
    for plan in plans:
        path = home / plan.target
        current = path.read_bytes()
        rendered = convert.filter_blocks(plan.captured, plan.target, manifest, groups)
        if current and not current.endswith(b"\n") and rendered == current + b"\n":
            with path.open("ab") as stream:
                stream.write(b"\n")
            print(f"Added a final newline to ~/{plan.target}, as its group marker needs one.")


def review(plans):
    """Print the change to each content file, then the limit of the secret check."""
    for plan in plans:
        diff = difflib.unified_diff(
            plan.newest.decode(errors="replace").splitlines(keepends=True),
            plan.captured.decode(errors="replace").splitlines(keepends=True),
            f"content/{plan.content_name}",
            f"content/{plan.content_name} (captured)",
        )
        print("".join(diff), end="")
    print("The secret check cannot prove that no secret remains. Review every added line.")


def run(home, names, display, update, confirm):
    """Capture NAMES into the active content. Return an exit code.

    UPDATE runs the normal update. CONFIRM(word, explanation) asks for the typed word.
    """
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
    newest = newest_commit(checkout)
    groups = profile.get("groups", {})
    items = convert.selected_paths(checkout, manifest, groups)
    plans, refused = [], []
    for name in names:
        try:
            content_name, now, captured = prepare(
                home, checkout, items, installed, newest, name, manifest, groups
            )
            plans.append(Plan(destination(home, name), content_name, now, captured))
        except Refused as error:
            refused.append(str(error))
    for message in refused:
        print(f"Not captured: {message}")
    if not plans:
        return 1
    review(plans)
    if not manager.has_terminal():
        raise ValueError(
            f"Capture needs a terminal. Run {display} capture in an interactive shell."
        )
    explanation = "Commit these files into content, then push when origin is yours."
    if not confirm(ANSWER, explanation):
        print("Cancelled. Nothing changed.")
        return 1 if refused else 0
    # Only now does the checkout move: fast-forward to the content captured onto.
    git(checkout, "merge", "-q", "--ff-only", newest)
    for plan in plans:
        target = checkout / plan.content_name
        mode = target.stat().st_mode & 0o777
        target.write_bytes(plan.captured)
        target.chmod(mode)
    shown = ", ".join(plan.target for plan in plans)
    machine = registry.machine_name(home)
    identity = ["-c", f"user.name={profile.get('git_name') or 'Fleet'}"]
    identity += ["-c", f"user.email={profile.get('git_email') or 'dotfleet@localhost'}"]
    git(checkout, "add", "--", *(plan.content_name for plan in plans))
    git(checkout, *identity, "commit", "-q", "-m", f"capture: {shown} from {machine}")
    print(f"Committed: capture: {shown} from {machine}")
    end_with_newline(home, plans, manifest, groups)
    if not push(checkout, display):
        print("Fleet did not run the update, because origin did not answer.")
        print(f"Run {display} update when origin answers.")
        return 1
    if refused:
        # A refused file is still drift. The update would offer to overwrite it and show it.
        print("Fleet did not run the update, because a file was refused.")
        print(f"Run {display} update after you resolve it.")
        return 1
    return update()


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
