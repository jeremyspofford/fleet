"""Declare the terminal interface's areas and their command actions.

Usage: import AREAS, ACTIONS, and RELATED. CI compares these routes with the CLI.
Help: python3 scripts/tui_navigation.py --help.
"""

import argparse
from pathlib import Path
import sys

# Keep this list explicit: the parity test must catch a new CLI command with no screen.
ACTIONS = {
    "status": ("Status — local state", ("status",)),
    "check": ("Check — fetch update results", ("check",)),
    "update": ("Update — pull and apply", ("update",)),
    "update-all": ("Update every fleet machine, this one first", ("update", "--all")),
    "diff": ("Diff — pending changes", ("diff",)),
    "diff-stat": ("Changed paths only", ("diff", "--stat")),
    "doctor": ("Doctor — check this machine", ("doctor",)),
    "topology": ("Topology — recorded connections", ("topology",)),
    "agents": ("Agents — declared items", ("agents", "list")),
    "agent-set": ("Turn an agent item on, off, or local", ("agents", "set")),
    "agent-import": ("Print entries from an agent's settings", ("agents", "import")),
    "domain": ("Domains — choices and current domain", ("domain", "list")),
    "domain-set": ("Choose a domain", ("domain", "set")),
    "groups": ("Groups — content switches", ("groups", "list")),
    "group-set": ("Turn a group on or off", ("groups", "set")),
    "pins": ("Tools — exact versions", ("tools", "list")),
    "pin-set": ("Install and select an exact version", ("tools", "set")),
    "tool-uninstall": ("Uninstall an extra version", ("tools", "uninstall")),
    "pin-remove": ("Remove a tool selection", ("tools", "remove")),
    "tool-update": ("Update tools to their newest versions", ("tools", "update")),
    "tool-adopt": ("Adopt installed versions into the record", ("tools", "adopt")),
    "secrets": ("Secrets — names and set/unset", ("secrets", "list")),
    "content": ("Content — checkout and Git state", ("content",)),
    "dotfiles": ("Dotfiles — downloaded repositories", ("dotfiles", "list")),
    "dotfiles-download": ("Download a dotfiles repository", ("dotfiles", "download")),
    "dotfiles-use": ("Activate a downloaded repository", ("dotfiles", "use")),
    "dotfiles-remove": ("Remove a downloaded repository", ("dotfiles", "remove")),
    "configure": ("Configure — reopen the terminal wizard", ("configure",)),
    "capture": ("Capture — copy an edited file into content", ("capture",)),
    "machines": ("List fleet members", ("machines", "list")),
    "machine-install": ("Deploy Fleet to a machine", ("machines", "install")),
    "machine-uninstall": ("Remove Fleet from a member", ("machines", "uninstall")),
    "add-connection": ("Pick an SSH host", ("machines", "set-ssh")),
    "name-connection": ("Set a name and SSH target", ("machines", "set-ssh")),
    "machine-export": ("Export this machine's record", ("machines", "export")),
    "machine-remove": ("Remove a stale machine record", ("machines", "remove")),
    "marketplaces": ("Fleet Marketplace — agent packages", ("marketplaces", "list")),
    "marketplace-add": ("Add a marketplace", ("marketplaces", "add")),
    "marketplace-delete": ("Delete a marketplace", ("marketplaces", "delete")),
    "marketplace-update": ("Update every marketplace", ("marketplaces", "update")),
    "logs": ("Logs — saved attempts", ("logs", "list")),
    "log-show": ("Latest attempt", ("logs", "show")),
    "backups": ("List backups", ("backups", "list")),
    "backup-delete-selected": ("Delete selected backups", ("backups", "delete")),
    "backup-delete-all": ("Delete all backups", ("backups", "delete", "--all")),
    "backup": ("Create a backup now", ("backups", "create")),
    "rollback": ("Restore a backup", ("backups", "restore")),
    "version": ("Version — manager and content", ("version",)),
    "addons": ("Add-ons — installed records", ("addons", "list")),
    "help": ("Help — all commands", ("help",)),
    "command-help": ("Help for one command or action", ("help",)),
    "tui": ("Interface help — keys and layout", ("tui", "--help")),
    "uninstall": ("Uninstall — review before removal", ("uninstall",)),
}

AREAS = {
    "Machine": ("status", "check", "update", "diff", "doctor", "topology"),
    "Configuration": (
        "domain",
        "groups",
        "agents",
        "marketplaces",
        "pins",
        "secrets",
        "content",
        "dotfiles",
        "configure",
        "capture",
    ),
    "Machines": (
        "machines",
        "machine-install",
        "add-connection",
        "name-connection",
        "machine-export",
        "machine-remove",
        "machine-uninstall",
    ),
    "Backups": ("backups", "backup", "rollback"),
    "Logs": ("logs", "log-show"),
    "System": ("version", "addons", "help", "tui", "uninstall"),
}

RELATED = {
    "help": ("command-help",),
    "update": ("update-all",),
    "domain": ("domain-set",),
    "groups": ("group-set",),
    "agents": ("agent-set", "agent-import"),
    "marketplaces": ("marketplace-add", "marketplace-update", "marketplace-delete"),
    "pins": ("pin-set", "pin-remove", "tool-uninstall", "tool-update", "tool-adopt"),
    "diff": ("diff-stat",),
    "dotfiles": ("dotfiles-download", "dotfiles-use", "dotfiles-remove"),
    "machines": (
        "machine-install",
        "add-connection",
        "name-connection",
        "machine-export",
        "machine-remove",
        "machine-uninstall",
    ),
    "backups": ("backup", "backup-delete-selected", "backup-delete-all"),
}

CHANGING = {
    "update",
    "update-all",
    "domain-set",
    "group-set",
    "agent-set",
    "marketplace-add",
    "dotfiles-download",
    "dotfiles-use",
    "dotfiles-remove",
    "marketplace-delete",
    "marketplace-update",
    "pin-set",
    "pin-remove",
    "tool-uninstall",
    "tool-update",
    "tool-adopt",
    "add-connection",
    "machine-install",
    "machine-uninstall",
    "name-connection",
    "machine-export",
    "machine-remove",
    "backup",
    "backup-delete-selected",
    "backup-delete-all",
    "rollback",
    "uninstall",
}


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
