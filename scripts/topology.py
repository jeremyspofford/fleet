"""Show this machine's recorded view of its registered connections. Never fetch.

Usage: fleet topology [--json]. JSON is a structured output format for scripts.
Contact observations stay in this machine's manager store; there is no shared registry.
Help: python3 scripts/topology.py --help.
"""

import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import textwrap

from common import atomic
import registry
import socket
import style
import updates

CONTACTS = ".fleet/contacts.json"


def load(home):
    path = home / CONTACTS
    if path.is_symlink() or not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError("Contact records must stay inside the manager store.")
    if not path.exists():
        return {"schema_version": 1, "machines": {}}
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("machines"), dict)
    ):
        raise ValueError("Invalid contact cache. Expected schema 1 and machine records.")
    for item in data["machines"].values():
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str)
            for key in ("target", "action", "state", "observed_at")
        ):
            raise ValueError("Invalid saved contact observation.")
        datetime.datetime.fromisoformat(item["observed_at"])
    return data


def remember(home, name, target, action, state, code):
    path = home / CONTACTS
    try:
        load(home)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            path.parent / ".contacts.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = load(home)
            data["machines"][name] = {
                "target": target,
                "action": action,
                "state": state,
                "exit_code": code,
                "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                    timespec="microseconds"
                ),
            }
            # Contact diagnostics retain one current observation for the newest 100 machines.
            data["machines"] = dict(
                sorted(
                    data["machines"].items(), key=lambda item: item[1]["observed_at"], reverse=True
                )[:100]
            )
            atomic(path, json.dumps(data, indent=2) + "\n")
    except (OSError, ValueError) as error:
        print("Warning: Could not save the contact observation: " + str(error))


def view(home):
    name = registry.machine_name()
    records = registry.load(home)["machines"]
    local = records.get(name) or registry.machine(home, "not recorded")
    root = {
        "name": name,
        "domain": local.get("domain") or "not recorded",
        "state": local.get("result", "not recorded"),
        "observed_at": local.get("last_run") or None,
    }
    if local.get("distro"):
        root["distro"] = local["distro"]
    try:
        cache = home / updates.CACHE
        if cache.is_symlink() or not cache.parent.resolve().is_relative_to(home.resolve()):
            raise ValueError("Check records must stay inside the manager store.")
        checked = json.loads(cache.read_text())
        if checked["schema_version"] != 1:
            raise ValueError("Unknown check cache schema.")
        datetime.datetime.fromisoformat(checked["checked_at"])
        if [item["name"] for item in checked["sources"]] != ["manager", "content"] or any(
            type(item["behind"]) is not int or item["behind"] < 0 for item in checked["sources"]
        ):
            raise ValueError("Invalid check observations.")
        root.update(
            state="updates available"
            if any(item["behind"] for item in checked["sources"])
            else "updates current",
            observed_at=checked["checked_at"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    contacts = load(home)["machines"]
    machines = []
    for key, record in sorted(records.items()):
        if key == name:
            continue
        target = record.get("ssh_target") or None
        contact = contacts.get(key, {})
        if not target or contact.get("target") != target:
            contact = {}
        machines.append(
            {
                "name": key,
                "ssh_target": target,
                "domain": record.get("domain") or "not recorded",
                "state": contact.get("state", "never contacted"),
                "observed_at": contact.get("observed_at"),
                # A WSL member names its distro. Other members leave the field out.
                **({"distro": record["distro"]} if record.get("distro") else {}),
            }
        )
    return {
        "schema_version": 1,
        "from": name,
        "scope": "Recorded observations only; no live checks. Other machines may have different views.",
        "this_machine": root,
        "machines": machines,
    }


def observation(item):
    if not item["observed_at"]:
        return "never contacted" if "ssh_target" in item else "no observation recorded"
    return item["state"] + " · recorded " + item["observed_at"]


def render(view):
    root = view["this_machine"]
    lines = [style.section("Topology from " + view["from"]), view["scope"], ""]
    distro = " · distro " + root["distro"] if root.get("distro") else ""
    lines += [root["name"] + distro + " · domain " + root["domain"], "  " + observation(root)]
    for index, item in enumerate(view["machines"]):
        last = index == len(view["machines"]) - 1
        branch, indent = ("└── ", "    ") if last else ("├── ", "│   ")
        target = item["ssh_target"]
        connection = (
            "no connection"
            if not target
            else target
            if target.startswith("wsl:")
            else "SSH uses this name"
            if target == item["name"]
            else "SSH " + target
        )
        distro = " · distro " + item["distro"] if item.get("distro") else ""
        label = item["name"] + distro + " · " + connection + " · domain " + item["domain"]
        lines += textwrap.wrap(
            label,
            100,
            initial_indent=branch,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        lines += textwrap.wrap(
            observation(item),
            100,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
    return "\n".join(lines) + "\n"


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
