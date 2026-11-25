"""Collect named command arguments through the private TUI channel.

Usage: arguments(action, home) returns CLI arguments, or None after cancellation.
The CLI validates choices and performs changes. This module does not apply files.
Help: python3 scripts/tui_actions.py --help.
"""

import argparse
from pathlib import Path
import sys

import command
import registry
import ui_channel
from tui_navigation import ACTIONS
import dotfiles_library


def field(name, label, default="", choices=None, required=True):
    return dict(name=name, label=label, default=default, choices=choices, required=required)


def form(
    title, fields, detail="Continue reviews the choice before any apply. Cancel changes nothing."
):
    answer = ui_channel.send(
        dict(kind="form", title=title, fields=fields, detail=detail), reply=True
    )
    if answer is None:
        return None
    if not isinstance(answer, dict) or set(answer) != {item["name"] for item in fields}:
        raise ValueError("Invalid form response. Nothing was applied.")
    for item in fields:
        value = answer[item["name"]]
        if not isinstance(value, str) or item["required"] and not value.strip():
            raise ValueError(item["label"] + " needs a value.")
        if item["choices"] and value not in [value for label, value in item["choices"]]:
            raise ValueError("Choose a listed value for " + item["label"] + ".")
    return answer


def choose(title, label, choices, default=None):
    if not choices:
        print("No choices available. Nothing changed.")
        return None
    answer = form(title, [field("choice", label, default or choices[0][1], choices)])
    return answer["choice"] if answer is not None else None


def help_arguments(home):
    from command_arguments import GROUPS

    selected = choose("Command help", "Command", [(name, name) for name in command.DESCRIPTIONS])
    if selected is None:
        return None
    if selected in GROUPS:
        action = choose(
            "Action help",
            "Action",
            [("All actions", "all"), *[(name, name) for name in GROUPS[selected]]],
        )
        return (
            ["help", selected, *([action] if action != "all" else [])]
            if action is not None
            else None
        )
    return ["help", selected]


def domain_arguments(home):
    saved = command.profile(home)
    selected = choose(
        "Choose a domain",
        "Domain",
        [
            (name + " — " + description, name)
            for name, description in command.domain_choices(home).items()
        ],
        saved.get("domain", "core"),
    )
    return ["domain", "set", "--name", selected] if selected is not None else None


def group_arguments(home):
    saved = command.profile(home)
    manifest = command.content_manifest(dotfiles_library.active(home))
    choices = [
        (name + " — " + spec["description"], name)
        for name, spec in manifest.get("groups", {}).items()
    ]
    selected = choose("Choose a group", "Content group", choices)
    if selected is None:
        return None
    state = choose(
        "Group: " + selected,
        "State",
        [("on — include its content", "on"), ("off — remove its content", "off")],
        "on" if saved.get("groups", {}).get(selected, True) else "off",
    )
    return ["groups", "set", "--name", selected, "--state", state] if state is not None else None


def agent_item_arguments(home):
    saved = command.profile(home)
    choices = [
        (key + " — " + item.spec.get("description", "skill folder"), key)
        for key, item in command.agent_items(home).items()
    ]
    selected = choose("Choose an agent item", "Agent item", choices)
    if selected is None:
        return None
    states = [
        ("on — Fleet manages it on this machine", "on"),
        ("off — remove Fleet's output on this machine", "off"),
    ]
    if not selected.startswith("skills."):
        states.append(("local — keep this machine's own value unmanaged", "local"))
    state = choose(
        "Agent item: " + selected,
        "State",
        states,
        saved.get("agent_items", {}).get(selected, "on"),
    )
    return ["agents", "set", "--name", selected, "--state", state] if state is not None else None


def agent_import_arguments(home):
    agents = [(name, name) for name in ("claude", "codex", "gemini", "opencode", "pi")]
    selected = choose("Import from an agent", "Agent", agents)
    return ["agents", "import", "--agent", selected] if selected is not None else None


def marketplace_add_arguments(home):
    answer = form(
        "Add a marketplace",
        [
            field("source", "Git URL or local path"),
            field("name", "Saved name (empty: repository name)", required=False),
            field("packages", "Packages, separated by commas (empty: all)", required=False),
        ],
    )
    if answer is None:
        return None
    arguments = ["marketplaces", "add", "--source", answer["source"].strip()]
    for key in ("name", "packages"):
        if answer[key].strip():
            arguments += ["--" + key, answer[key].strip()]
    return arguments


def marketplace_delete_arguments(home):
    import marketplaces

    names = sorted(marketplaces.load_records(home)["marketplaces"])
    selected = choose("Delete a marketplace", "Marketplace", [(name, name) for name in names])
    return ["marketplaces", "delete", "--name", selected] if selected is not None else None


def dotfiles_source_arguments(home):
    answer = form(
        "Move content to a new address",
        [field("repo", "Git address of the content repository")],
        "The next update moves origin there and keeps the old one as upstream. Cancel stops.",
    )
    if answer is None:
        return None
    return ["dotfiles", "source", "--repo", answer["repo"].strip()]


def dotfiles_download_arguments(home):
    answer = form(
        "Download a dotfiles repository",
        [
            field("url", "Git URL or local repository path"),
            field("name", "Name in the library (empty: repository name)", required=False),
        ],
        "Downloading changes no applied file. Use activates a download later. Cancel stops.",
    )
    if answer is None:
        return None
    arguments = ["dotfiles", "download", answer["url"].strip()]
    if answer["name"].strip():
        arguments += ["--name", answer["name"].strip()]
    return arguments


def dotfiles_choice(home, title, action, inactive):
    current = dotfiles_library.active_name(home)
    names = [name for name in dotfiles_library.names(home) if not inactive or name != current]
    selected = choose(title, "Download", [(name, name) for name in names])
    return ["dotfiles", action, selected] if selected is not None else None


def dotfiles_use_arguments(home):
    return dotfiles_choice(home, "Activate a downloaded repository", "use", True)


def dotfiles_remove_arguments(home):
    return dotfiles_choice(home, "Remove a downloaded repository", "remove", True)


def pin_arguments(home):
    answer = form(
        "Install and select a tool version",
        [
            field("tool", "Tool name, such as node"),
            field("version", "Exact version, such as 24.14.1"),
        ],
        "This explicit action installs the exact version you choose. Cancel changes nothing.",
    )
    return ["tools", "set", answer["tool"], "--version", answer["version"]] if answer else None


def unpin_arguments(home):
    saved = command.profile(home)
    answer = form(
        "Remove a tool selection",
        [field("tool", "Tool name", next(iter(saved.get("pins", {})), ""))],
        "Remove this selection and block its content defaults. Installed software stays. Cancel stops.",
    )
    return ["tools", "remove", answer["tool"]] if answer is not None else None


def uninstall_tool_arguments(home):
    answer = form(
        "Uninstall an extra tool version",
        [
            field("tool", "Tool name, such as node"),
            field("version", "Exact version, such as 26.3.1"),
        ],
        "Only extra installed versions can be removed. A typed confirmation follows the review.",
    )
    return (
        ["tools", "uninstall", answer["tool"], "--version", answer["version"]] if answer else None
    )


def remove_machine_arguments(home):
    selected = choose(
        "Remove a stale record",
        "Machine",
        [
            (name, name)
            for name in registry.load(home)["machines"]
            if name != registry.machine_name(home)
        ],
    )
    return ["machines", "remove", "--name", selected] if selected is not None else None


def uninstall_machine_arguments(home):
    members = [
        (name, name)
        for name, record in registry.load(home)["machines"].items()
        if record["enrolled"] and record.get("ssh_target") and name != registry.machine_name(home)
    ]
    selected = choose("Remove Fleet from a member", "Machine", members)
    return ["machines", "uninstall", selected] if selected is not None else None


def export_arguments(home):
    answer = form(
        "Export this machine",
        [field("output", "New file path; leave empty to view only", required=False)],
        "A file saves a private record for transfer. No path shows the record without saving. Cancel stops.",
    )
    return (
        ["machines", "export", *(["--output", answer["output"]] if answer["output"] else [])]
        if answer is not None
        else None
    )


def connection_arguments(home):
    answer = form(
        "Name an SSH connection",
        [
            field("name", "Machine name; empty uses the target", required=False),
            field("target", "SSH target; empty uses the name", required=False),
        ],
        "Give at least one value. Fleet saves it, then tests SSH once. Cancel stops.",
    )
    if answer is None:
        return None
    if not any(answer.values()):
        raise ValueError("Give a machine name or an SSH target. Nothing saved.")
    return [
        "machines",
        "set-ssh",
        *(["--name", answer["name"]] if answer["name"] else []),
        *(["--target", answer["target"]] if answer["target"] else []),
    ]


FORMS = {
    "command-help": help_arguments,
    "domain-set": domain_arguments,
    "group-set": group_arguments,
    "agent-set": agent_item_arguments,
    "agent-import": agent_import_arguments,
    "marketplace-add": marketplace_add_arguments,
    "dotfiles-download": dotfiles_download_arguments,
    "dotfiles-use": dotfiles_use_arguments,
    "dotfiles-remove": dotfiles_remove_arguments,
    "dotfiles-source": dotfiles_source_arguments,
    "marketplace-delete": marketplace_delete_arguments,
    "pin-set": pin_arguments,
    "pin-remove": unpin_arguments,
    "tool-uninstall": uninstall_tool_arguments,
    "machine-remove": remove_machine_arguments,
    "machine-uninstall": uninstall_machine_arguments,
    "machine-export": export_arguments,
    "name-connection": connection_arguments,
}


def arguments(action, home):
    return FORMS[action](home) if action in FORMS else list(ACTIONS[action][1])


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
