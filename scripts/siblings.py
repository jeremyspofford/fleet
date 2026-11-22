"""Find and register sibling WSL distros on this Windows host. Reach them through wsl.exe.

Usage: import register or distros from this module.
A distro is a sibling only when its own Fleet answers with a record. Others never enter the registry.
Fleet never creates, deletes, or configures a distro, and never edits /etc/wsl.conf.
Help: python3 scripts/siblings.py --help.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import connections


def own_distro():
    return os.environ.get("WSL_DISTRO_NAME", "")


def interop():
    """Return True when this is a WSL distro that can reach wsl.exe."""
    return bool(own_distro()) and shutil.which("wsl.exe") is not None


def distros():
    """List this host's distros through wsl.exe --list --verbose. Return [] when it fails."""
    try:
        result = subprocess.run(
            ["wsl.exe", "--list", "--verbose"],
            capture_output=True,
            env=connections.environment(sibling=True),
            timeout=connections.TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    # Older wsl.exe builds print UTF-16 even when WSL_UTF8 asks for UTF-8.
    raw = result.stdout
    text = raw.decode("utf-16-le", "replace") if b"\0" in raw else raw.decode("utf-8", "replace")
    names = []
    for line in text.splitlines()[1:]:
        words = line.replace("*", " ").split()
        if words:
            names.append(words[0])
    return names


def match(name, available=None):
    """Return the distro that NAME names, ignoring case, or an empty string."""
    available = distros() if available is None else available
    return next((item for item in available if item.lower() == name.lower()), "")


def register(home):
    """Register each enrolled sibling here, and this machine on each sibling, in both directions.

    Each side's own manager writes its own registry. Return the sibling names that were registered.
    """
    import deploy

    if not own_distro():
        return []
    if not interop():
        print("Siblings not checked: wsl.exe is unavailable. Next: " + connections.INTEROP_STEP)
        return []
    registered = []
    for distro in distros():
        # Docker Desktop's own distros never hold Fleet. Leave them stopped.
        if distro == own_distro() or distro.startswith("docker-desktop"):
            continue
        target = connections.WSL + distro
        try:
            facts = deploy.facts_of(distro, target)
        except deploy.DeployError:
            # A distro without Fleet, or one that does not answer, is not a sibling machine.
            continue
        if not facts.get("enrolled"):
            continue
        try:
            deploy.register_caller(home, target, distro, None, False, announce=False)
            peer = deploy.peer_record(home) | {"ssh_target": connections.WSL + own_distro()}
            deploy.remote_json(distro, target, {"version": 3, "action": "join", "peer": peer})
        except deploy.DeployError as error:
            print(f"Sibling {facts['hostname']} not registered both ways: {' '.join(error.lines)}")
            continue
        print(f"Sibling {facts['hostname']} registered both ways through wsl.exe ({distro}).")
        registered.append(facts["hostname"])
    return registered


def usage():
    import argparse

    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
