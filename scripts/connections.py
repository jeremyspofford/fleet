"""Read SSH host names and test existing connections without configuring them.

Usage: fleet machines set-ssh --target mini-pc; fleet machines set-ssh.
SSH is the secure connection tool. Fleet reads its config and uses existing keys.
Help: python3 scripts/connections.py --help.
"""

import glob
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import registry

TIMEOUT = 10
OPTIONS = (
    "BatchMode=yes",
    "ConnectTimeout=10",
    "ConnectionAttempts=1",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=2",
    "ClearAllForwardings=yes",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ForkAfterAuthentication=no",
    "StdinNull=no",
)


def hosts(home):
    """Collect literal Host tokens, including Include files. Never evaluate Match commands."""
    found, visited = [], set()

    def read(path):
        path = path.resolve()
        if path in visited or not path.is_file():
            return
        visited.add(path)
        for line in path.read_text().splitlines():
            match = re.match(r"\s*(Host|Include)(?:\s+|=)(.*)$", line, re.IGNORECASE)
            if not match:
                continue
            try:
                values = shlex.split(match[2].lstrip("= "), comments=True)
            except ValueError:
                continue  # SSH reports malformed connection settings when testing the choice.
            if match[1].lower() == "host":
                for value in values:
                    if value.startswith("!") or any(char in value for char in "*?"):
                        continue
                    if value not in found:
                        found.append(value)
            else:
                for value in values:
                    value = os.path.expandvars(value.replace("%d", str(home)))
                    if value.startswith("~/"):
                        value = str(home / value[2:])
                    path_pattern = Path(value).expanduser()
                    if not path_pattern.is_absolute():
                        path_pattern = home / ".ssh" / path_pattern
                    for included in sorted(glob.glob(str(path_pattern))):
                        read(Path(included))

    read(home / ".ssh/config")
    return found


def choices(home):
    records = registry.load(home)["machines"]
    registered = set(records) | {record.get("ssh_target", "") for record in records.values()}
    concrete = hosts(home)
    available = [host for host in concrete if host not in registered]
    if not (home / ".ssh/config").exists():
        notice = "No SSH config file found. Type a target instead."
    elif not concrete:
        notice = "No concrete SSH hosts found. Type a target instead."
    elif not available:
        notice = "All concrete SSH hosts are registered. Type another target instead."
    else:
        notice = ""
    return available, notice


def install_choices(home):
    """Offer saved connections and SSH config hosts that are not fleet members yet."""
    records = registry.load(home)["machines"]
    members = {name for name, record in records.items() if record["enrolled"]}
    members |= {records[name].get("ssh_target", "") for name in members}
    saved = [
        name
        for name, record in records.items()
        if not record["enrolled"] and record.get("ssh_target")
    ]
    concrete = [host for host in hosts(home) if host not in members and host not in saved]
    available = sorted(saved) + concrete
    notice = (
        "" if available else "No saved connections or SSH config hosts are left. Type a target."
    )
    return available, notice


def pick(home, purpose="connect"):
    import manager
    import ui_channel

    if not ui_channel.active() and not manager.has_terminal():
        raise ValueError("No terminal attached. Use machines set-ssh --target HOST or --name NAME.")
    available, notice = install_choices(home) if purpose == "install" else choices(home)
    title = "Deploy Fleet to a machine" if purpose == "install" else "Add an SSH connection"
    detail = "Choose a listed host, or type user@host or user@IP. Cancel makes no changes."
    if ui_channel.active():
        return ui_channel.send(
            {
                "kind": "choose",
                "title": title,
                "items": available,
                "notice": notice,
                "detail": detail,
            },
            reply=True,
        )
    from style import section

    print(section(title))
    if notice:
        print(notice)
    if available:
        for number, host in enumerate(available, 1):
            print(f"  {number}. {host}")
        answers = [str(number) for number in range(1, len(available) + 1)] + ["type", "cancel"]
        chosen = manager.ask(
            "Host number",
            "cancel",
            answers,
            detail=manager.options(
                [
                    ("NUMBER", "Use the host with that number in the list above."),
                    ("type", "Type another SSH target next."),
                    ("cancel", "Stop without connecting."),
                ],
                "cancel",
                None,
            ),
        )
        if chosen == "cancel":
            return None
        if chosen != "type":
            return available[int(chosen) - 1]
    target = manager.ask(
        "SSH target",
        "cancel",
        detail=detail
        + "\nType the target, such as user@host.\n  cancel  Stop without connecting."
        + "\nPress Enter alone to choose cancel.",
    )
    return None if target == "cancel" else target


# A sibling WSL distro on this Windows host is reached through wsl.exe, never through SSH.
WSL = "wsl:"
INTEROP_STEP = "enable interop in /etc/wsl.conf ([interop] enabled = true), then restart WSL."


def is_sibling(target):
    return bool(target) and target.startswith(WSL)


def argv(target, command, terminal=False):
    """Return the command line that runs COMMAND on TARGET: SSH, or wsl.exe for a sibling."""
    if is_sibling(target):
        # The distro starts if it is stopped. --exec skips the default shell, so quoting holds.
        return ["wsl.exe", "-d", target[len(WSL) :], "--cd", "~", "--exec", "sh", "-c", command]
    arguments = ["ssh", "-t" if terminal else "-T"]
    for option in OPTIONS:
        arguments.extend(["-o", option])
    return arguments + ["--", target, command]


def environment(sibling=False):
    """Return the environment for SSH or wsl.exe. wsl.exe sees WSL_UTF8 only through WSLENV."""
    result = dict(os.environ, SSH_ASKPASS_REQUIRE="never", LC_ALL="C", WSL_UTF8="1")
    if sibling:
        shared = [name for name in os.environ.get("WSLENV", "").split(":") if name]
        result["WSLENV"] = ":".join([*shared, "WSL_UTF8"] if "WSL_UTF8" not in shared else shared)
    return result


def decoded(data):
    """Decode command output. wsl.exe errors may be UTF-16-LE; a bad byte never raises."""
    if not data:
        return ""
    return data.decode("utf-16-le" if b"\0" in data else "utf-8", errors="replace")


def invoke(target, command, payload=None, *, probe=False):
    result = subprocess.run(
        argv(target, command),
        input=(payload if payload is not None else "").encode(),
        capture_output=True,
        env=environment(is_sibling(target)),
        timeout=TIMEOUT if probe else None,
    )
    result.stdout, result.stderr = decoded(result.stdout), decoded(result.stderr)
    return result


def one_line(text):
    return " ".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).split())


def failure(home, name, target, error):
    """Return one error line and one next step, using the host OS for missing SSH."""
    if is_sibling(target):
        if isinstance(error, FileNotFoundError):
            return "wsl.exe is unavailable; Windows interop is off", INTEROP_STEP
        return "the sibling distro did not answer: " + one_line(str(error))[:200], (
            f"run wsl.exe -d {target[len(WSL) :]} once to check the distro; then retry."
        )
    if isinstance(error, FileNotFoundError):
        system = registry.os_name()
        step = (
            "enable the OpenSSH Client optional feature in Windows Settings."
            if system == "Windows"
            else "restore the built-in OpenSSH client on macOS."
            if system == "macOS"
            else "install the OpenSSH client with your Linux package manager."
        )
        return "SSH is not installed", step
    if isinstance(error, subprocess.TimeoutExpired):
        return (
            "SSH connection test timed out",
            "check that the target is awake and reachable; then retry.",
        )
    message = one_line(str(error))
    lowered = message.lower()
    if any(
        text in lowered
        for text in (
            "could not resolve hostname",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "no such host is known",
        )
    ):
        reason = (
            "SSH config entry exists, but its host does not resolve"
            if target in hosts(home)
            else "target is not in your SSH config and does not resolve as a host"
        )
        return reason, "use --target user@host or user@IP, or add a Host entry in ~/.ssh/config."
    if "permission denied" in lowered or "authentication failed" in lowered:
        return (
            "SSH authentication failed",
            f"run ssh-keygen if needed, then ssh-copy-id {target}; on Windows targets, add the public key to authorized_keys (see README).",
        )
    if "host key verification failed" in lowered or "host identification has changed" in lowered:
        return (
            "SSH host key verification failed",
            "verify the target's host key with its owner before connecting with SSH.",
        )
    for marker, reason in (
        ("connection refused", "SSH connection refused"),
        ("timed out", "SSH connection timed out"),
        ("no route to host", "SSH target is unreachable"),
        ("network is unreachable", "SSH target is unreachable"),
    ):
        if marker in lowered:
            return reason, "check that the target is awake and reachable; then retry."
    return "SSH connection failed", "check the target's SSH access; then retry."


def report_failure(home, name, target, error):
    reason, step = failure(home, name, target, error)
    print(f"Error: {name}: {reason}.")
    print("Next: " + step)
    return reason


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
