"""Read manager-owned stores as human rows. Never execute a secrets file.

Usage: use dotfleet status, backups list, secrets list, content, or machines export.
"""

import os
from pathlib import Path
import shlex

from common import atomic, read_toml, run
import registry
from command_display import command_name, current_command
import dotfiles_library


def profile_rows(home, profile):
    display = command_name(profile)
    rows = [
        ("Profile file", str(home / ".fleet/profile.toml")),
        ("Domain", profile.get("domain", "core")),
        (
            "Domain choice",
            "chosen"
            if profile.get("domain_chosen", False)
            else f"UNCHOSEN; run {display} domain list",
        ),
        ("Git name", profile.get("git_name") or f"missing; run {display} configure"),
        ("Git email", profile.get("git_email") or f"missing; run {display} configure"),
    ]
    for field, label in [("pins", "Tool"), ("groups", "Group"), ("agents", "Agent")]:
        rows += [
            (label + " " + name, value) for name, value in sorted(profile.get(field, {}).items())
        ] or [(field.capitalize(), "none")]
    rows.append(
        (
            "Edit choices",
            f"Use {display} domain set, tools set, groups set, or configure. Do not edit profile.toml.",
        )
    )
    return rows


def backup_entities(home, folders):
    """Describe each backup as a row of name, result, and size.

    Backups without a run result form their own section.
    """
    import json

    from backup_cleanup import details

    entities = []
    for folder in reversed(folders):
        record_path = folder / "RUN.json"
        record = json.loads(record_path.read_text()) if record_path.is_file() else {}
        size = f"{details(folder)['size']:,} bytes"
        result = record.get("result")
        entities.append(
            {"name": folder.name, "row": (folder.name, result, size)}
            if result
            else {
                "name": folder.name,
                "row": (folder.name, size),
                "group": "Backups without a run result",
            }
        )
    return entities


def secret_rows(home, manifest):
    names = sorted(manifest.get("secrets", {}))
    present = {name: bool(os.environ.get(name)) for name in names}
    path = home / ".secrets"
    if path.is_file():
        # shlex reads literal shell quoting. No shell expansion or command execution occurs.
        try:
            words = shlex.split(path.read_text(), comments=True)
        except ValueError:
            raise ValueError(
                "Cannot read secret assignments. No secret values were printed."
            ) from None
        for word in words:
            name, separator, value = word.partition("=")
            if separator and name in present:
                present[name] = bool(value)
    return [(name, "set" if present[name] else "unset") for name in names] or [
        ("Declared variables", "none")
    ]


def content_rows(home, profile, checkout_state):
    checkout = dotfiles_library.active(home)
    return [
        ("Checkout", str(checkout)),
        ("Repository", profile["content_repo"]),
        ("Commit and behind", checkout_state(checkout)),
        ("Git status", run("git", "-C", str(checkout), "status", "--short").strip() or "clean"),
    ]


def export_rows(home, version, output=None):
    name = registry.machine_name(home)
    data = registry.load(home)
    record = data["machines"].get(name) or registry.machine(home, version)
    rows = [("Machine", name + " · this machine")]
    labels = {
        "os": "OS",
        "content_repo": "Content repo",
        "content_commit": "Content commit",
        "manager_version": "Manager version",
        "last_run_record": "Run record",
    }
    rows += [
        (labels.get(key, key.replace("_", " ").capitalize()), value)
        for key, value in record.items()
        if key != "hostname"
    ]
    if output:
        destination = Path(output).expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError("Export destination already exists. Choose a new file.")
        atomic(destination, registry.serialize({"schema_version": 1, "machines": {name: record}}))
        rows.append(("Export file", str(destination)))
    else:
        rows.append(
            (
                "Carry this record",
                f"Use {current_command(home)} machines export --output FILE to save a private TOML copy.",
            )
        )
    return rows


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    from style import ArgumentParser

    argument_parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from pathlib import Path
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
