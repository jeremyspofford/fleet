"""Define command groups, named parameters, and consistent help spellings.

Usage: import add_parameters, add_groups, and help_arguments from the dispatcher.
Help: python3 scripts/command_arguments.py --help.
"""

import argparse
from pathlib import Path
import sys

import remote

GROUPS = {
    "agents": {
        "import": "Print agents-table entries from one agent's existing settings.\n"
        "Write nothing. Secret values are withheld; each becomes a variable name to set.",
        "list": "List declared agent items and their state on this machine.\n"
        "An item is one skill, rule set, server, connector, plugin, or hook.",
        "set": "Turn one agent item on or off, or keep this machine's own value.\n"
        "The profile saves the choice; the update applies it.",
    },
    "marketplaces": {
        "add": "Add a marketplace and install its packages.\n"
        "Take every package, named packages, or named features where a package allows it.\n"
        "The review lists every output before the apply.",
        "delete": "Delete a marketplace, its package copy, and its installed outputs.",
        "update": "Move marketplaces to their newest commit, or change a pin or selection.\n"
        "A pin blocks updates; --pin moves the pin and --unpin clears it.\n"
        "Without --name, update every marketplace.",
        "list": "List marketplaces with their source, version, pin, and installed packages.\n"
        "Read saved records and the last check only.",
    },
    "logs": {
        "list": "List the newest change-making attempts first.\nLimit the number with --limit.",
        "show": "Show the latest attempt, or select its saved timestamp or backup with --name.\n"
        "Use --json for the index entry and its RUN.json, when that record still exists.",
    },
    "machines": {
        "list": "List saved machines and their connections.\nMake no network requests.",
        "set-ssh": "Save and test an SSH target.\nOmit flags to pick a host in a terminal.",
        "export": "Show this machine's saved record, or save a private copy with --output.",
        "install": "Deploy Fleet to another machine and enroll it as a fleet member.\n"
        "Probe it over SSH, bootstrap Python, run its wizard here, and register both ways.\n"
        "Without NAME, pick a host from your SSH config or saved connections.",
        "remove": "Remove a stale machine record.\nKeep the active machine.",
        "uninstall": "Remove Fleet from another fleet member and drop it from the fleet.\n"
        "Type uninstall to confirm, then press Enter. Its content, backups, and own files stay.",
    },
    "backups": {
        "restore": "List backups or restore one after typing restore to confirm.\n"
        "Restore the saved profile too. Press Enter after each answer.",
        "delete": "Delete saved backups after typing delete to confirm.\n"
        "Deleted backups cannot be rolled back to.",
        "create": "Copy managed files and the profile into a new private backup.\n"
        "Change no managed files.\nPrint the backup location and run record.",
        "list": "List saved copies with their dates, results, sizes, and locations.",
    },
    "secrets": {"list": "List declared variable names as set or unset.\nNever print a value."},
    "tools": {
        "list": "List recorded tools with their install state and extra versions.\n"
        "List other installed tools in their own section.",
        "set": "Install and record an exact tool version.",
        "remove": "Remove a recorded version without uninstalling the tool.",
        "uninstall": "Uninstall one installed extra version through Mise.\n"
        "Type uninstall to confirm, then press Enter.\n"
        "A recorded version must first be removed from choices or replaced with tools set.",
        "update": "Record and install the newest available version of selected tools.\n"
        "Mise finds the newest version; the profile records that exact version.\n"
        "Without TOOL, update every selected tool, then print a summary.\n"
        "A current tool stays unchanged.\n"
        "One failed tool does not stop the others.\n"
        "A tool that is not selected fails; select it first with tools set.",
        "adopt": "Record the installed version of each tool that records no version.\n"
        "A tool with several versions adopts the one Mise reports as active.\n"
        "Adopt skips a tool when no version is active or the version is not exact.\n"
        "Adopt never replaces a recorded version. It installs and uninstalls nothing.\n"
        "Without TOOL, adopt every installed tool except those removed with tools remove.",
    },
    "groups": {
        "list": "List content groups, their state, and what each adds.",
        "set": "Turn one content group on or off, then apply the change.",
    },
    "domain": {
        "list": "List content domains and show the chosen domain.",
        "set": "Choose a domain and apply its content additions.",
    },
    "addons": {"list": "List installed add-on records.\nNo add-ons ship with Fleet."},
    "dotfiles": {
        "download": "Download a dotfiles repository into ~/.fleet/dotfiles.\n"
        "Downloading never activates it and changes no applied file.\n"
        "Without --name, the repository's name names the download.",
        "list": "List downloaded repositories, their sources, and the active one.",
        "remove": "Delete a downloaded repository.\n"
        "The active repository cannot be removed.\nType remove to confirm, then press Enter.",
        "use": "Activate a downloaded repository.\n"
        "Review its file changes, then type use to confirm. A backup comes first.\n"
        "Files that only the old repository managed go, unless they have local edits.\n"
        "Domain, groups, tools, and agents carry over. An undeclared domain applies core.",
    },
}

EXAMPLES = {
    "dotfiles": "dotfiles list; dotfiles download https://github.com/example/dotfiles; dotfiles download https://github.com/example/dotfiles --name work; dotfiles use work; dotfiles remove default",
    "marketplaces": "marketplaces list; marketplaces add --source https://github.com/example/agent-market; marketplaces add --source URL --packages review-kit; marketplaces add --source URL --features review-kit/mcp.docs; marketplaces update; marketplaces update --name agent-market --pin v1.2.0; marketplaces update --name agent-market --unpin; marketplaces delete --name agent-market",
    "agents": "agents import --agent claude; agents list; agents set --name mcp.docs --state off; agents set --name mcp.docs --state local",
    "logs": "logs list; logs list --limit 10; logs show; logs show --name BACKUP; logs show --json",
    "machines": "machines install mini-pc; machines install lab --target me@10.0.0.7 --return-target me@desk; machines list; machines set-ssh --target mini-pc; machines set-ssh --name beelink --target mini-pc; machines export --output FILE; machines remove --name NAME; machines uninstall mini-pc",
    "domain": "domain list; domain set --name personal",
    "groups": "groups list; groups set --name docker --state off",
    "tools": "tools list; tools set node --version 26.13.1; tools remove node; tools uninstall node --version 26.3.1; tools update; tools update node; tools adopt; tools adopt jq",
    "backups": "backups create; backups list; backups restore --backup BACKUP; backups delete --name BACKUP; backups delete --keep 2; backups delete --all",
    "secrets": "secrets list",
    "capture": "capture ~/.zshrc; capture .zshrc .config/git/ignore",
    "content": "content",
    "addons": "addons list",
    "topology": "topology; topology --json",
    "help": "help machines; machines set-ssh help",
}


# Help never shows exit codes, by owner ruling on 2026-11-18. Commands still return these codes:
#   0    Success, help, or cancellation. Check: manager and content are current. Diff: no differences.
#   1    Operation failed. Check: updates are available. Diff: differences exist.
#        Doctor: one or more checks failed.
#        Tools update: one or more tools failed, or the tool is not selected.
#   2    Invalid arguments. Check: could not check updates. Diff: preview failed.
#        Machines set-ssh: connection test failed; the saved target stays.
#   130  Interrupted.
# Diff --stat returns the same codes as diff. A failed remote connection returns nonzero.
# Capture returns 0 when captured, or when cancelled.
# Capture returns 1 when a file is refused, or when none is left to capture.
# Capture returns 1 when the commit fails; the checkout is then put back.
# Capture returns 1 when origin changed during the review, or did not take the commit.
# A refused file still lets the others be captured. Otherwise capture returns the update's code.
def help_epilog(name, display, action=None):
    choices = EXAMPLES.get(name, name).split("; ")
    if action:
        choices = [value for value in choices if value.split()[:2] == [name, action]]
    examples = ["  " + display + " " + value for value in choices]
    return "Examples:\n" + "\n".join(examples) if examples else None


def add_groups(parser, name, formatter, display):
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    parser.set_defaults(help_parser=parser)
    for action, description in GROUPS[name].items():
        child = actions.add_parser(
            action,
            prog=parser.prog + " " + action,
            description=description
            + (
                "\nEither flag supplies both fields unless the values differ."
                "\nTest SSH once in batch mode, without password prompts, with a ten-second timeout."
                "\nA failed test keeps the saved target."
                "\nFleet never edits SSH configuration."
                if action == "set-ssh"
                else ""
            ),
            help=description.splitlines()[0],
            formatter_class=formatter,
            epilog=help_epilog(name, display, action),
        )
        child.set_defaults(help_parser=child)
        add_action_parameters(child, name, action)


def add_action_parameters(parser, name, action):
    if name in remote.PROFILE_ACTIONS and action in remote.PROFILE_ACTIONS[name]:
        parser.add_argument(
            "--machine",
            help="registered machine; save there; its next update applies the change",
        )
    if name == "logs":
        if action == "list":
            parser.add_argument(
                "--limit", type=int, default=100, help="newest N attempts; default 100"
            )
        else:
            parser.add_argument(
                "--json", action="store_true", help="print both records as JSON for scripts"
            )
            parser.add_argument(
                "--name", help="saved timestamp or backup folder name; default latest"
            )
    elif name == "backups" and action == "delete":
        selectors = parser.add_mutually_exclusive_group(required=True)
        selectors.add_argument(
            "--all", dest="all_backups", action="store_true", help="delete every saved backup"
        )
        selectors.add_argument(
            "--keep", type=int, help="keep the newest N backups; delete older ones; zero keeps none"
        )
        selectors.add_argument("--name", help="one folder name from backups list")
    elif name == "backups" and action == "restore":
        parser.add_argument("--backup", help="saved folder name or path; omit to list and choose")
    elif name == "machines":
        if action == "set-ssh":
            # Keep the specifically promised old registration syntax out of help.
            parser.add_argument("legacy_name", nargs="?", help=argparse.SUPPRESS)
            parser.add_argument("legacy_target", nargs="?", help=argparse.SUPPRESS)
            parser.add_argument("--name", dest="hostname", help="saved name; defaults to --target")
            parser.add_argument(
                "--target", dest="ssh_target", help="SSH host or user@host; defaults to --name"
            )
        elif action == "remove":
            parser.add_argument("--name", dest="hostname", required=True, help="saved machine name")
        elif action == "uninstall":
            parser.add_argument("name", help="fleet member from machines list")
        elif action == "install":
            import manager

            parser.add_argument(
                "name",
                nargs="?",
                help="SSH config host or local WSL distro to deploy to; the machine names itself",
            )
            parser.add_argument(
                "--machine-name",
                help="the new machine's fleet name; required for a scripted WSL deploy",
            )
            parser.add_argument(
                "--via",
                choices=["wsl", "ssh"],
                help="wsl: a distro on this host; ssh: an SSH host; needed when NAME matches both",
            )
            parser.add_argument("--domain", type=manager.domain_argument, help="scripted domain")
            parser.add_argument("--git-email", help="scripted Git author email")
            parser.add_argument("--git-name", help="scripted Git author name")
            parser.add_argument("--repo", help="content Git URL for the target; default: Fleet's")
            parser.add_argument(
                "--return-target", help="SSH target that reaches this machine from NAME"
            )
            parser.add_argument(
                "--target",
                dest="ssh_target",
                help="user@host; a distro as wsl:DISTRO or with --via wsl; default: NAME",
            )
            parser.add_argument(
                "--tool",
                action="append",
                default=[],
                type=manager.pin_argument,
                metavar="TOOL=VERSION",
                help="scripted exact tool version; repeat for more tools",
            )
            parser.add_argument(
                "--update-bash",
                action="store_true",
                help="accept the Homebrew bash install when the target's bash is older than 4.4",
            )
        elif action == "export":
            parser.add_argument("--output", help="new private TOML file; omit to show the record")
    elif name == "marketplaces" and action == "add":
        parser.add_argument("--features", help="PACKAGE/KIND.NAME list, separated by commas")
        parser.add_argument("--name", help="saved name; default: the repository name")
        parser.add_argument("--packages", help="package names, separated by commas; default: all")
        parser.add_argument("--pin", help="tag or commit to keep; the pin blocks updates")
        parser.add_argument("--source", required=True, help="marketplace Git URL or local path")
    elif name == "marketplaces" and action == "update":
        selectors = parser.add_mutually_exclusive_group()
        selectors.add_argument(
            "--all", dest="take_all", action="store_true", help="take every package"
        )
        parser.add_argument("--features", help="PACKAGE/KIND.NAME list, separated by commas")
        parser.add_argument("--name", help="one marketplace; default: every marketplace")
        selectors.add_argument("--packages", help="package names, separated by commas")
        pins = parser.add_mutually_exclusive_group()
        pins.add_argument("--pin", help="tag or commit to keep; the pin blocks updates")
        pins.add_argument("--unpin", action="store_true", help="clear the pin and update")
    elif name == "marketplaces" and action == "delete":
        parser.add_argument("--name", required=True, help="marketplace from marketplaces list")
    elif name == "agents" and action == "import":
        parser.add_argument(
            "--agent",
            required=True,
            choices=["claude", "codex", "gemini", "opencode", "pi"],
            help="agent whose user-level settings to read",
        )
    elif name == "agents" and action == "set":
        parser.add_argument("--name", required=True, help="item from agents list, such as mcp.docs")
        parser.add_argument(
            "--state",
            required=True,
            choices=["local", "off", "on"],
            help="on: Fleet manages it; off: removed here; local: keep this machine's value",
        )
    elif name == "dotfiles" and action == "download":
        parser.add_argument("url", help="Git URL or local repository path")
        parser.add_argument("--name", help="download name; default: the repository's name")
    elif name == "dotfiles" and action in ("use", "remove"):
        parser.add_argument("name", help="download from dotfiles list")
    elif name == "domain" and action == "set":
        parser.add_argument("--name", required=True, help="domain from domain list")
    elif name == "groups" and action == "set":
        parser.add_argument("--name", required=True, help="content group from groups list")
        parser.add_argument(
            "--state", required=True, choices=["on", "off"], help="include or remove content"
        )
    elif name == "tools" and action == "update":
        parser.add_argument(
            "tool",
            nargs="?",
            help="selected tool, such as node; omit to update every selected tool",
        )
    elif name == "tools" and action == "adopt":
        parser.add_argument(
            "tool",
            nargs="?",
            help="installed tool, such as jq; omit to adopt every unrecorded tool",
        )
    elif name == "tools" and action in ("set", "remove", "uninstall"):
        parser.add_argument("tool", help="tool name, such as node")
        if action in ("set", "uninstall"):
            parser.add_argument(
                "--version", required=True, help="exact tool version, such as 24.14.1"
            )


def add_parameters(parser, name):
    if name == "capture":
        parser.add_argument(
            "file", nargs="+", help="edited managed home file, such as ~/.zshrc or .zshrc"
        )
    if name == "diff":
        parser.add_argument("--stat", action="store_true", help="show paths only")
    elif name == "topology":
        parser.add_argument(
            "--json", action="store_true", help="print JSON, a structured format for scripts"
        )
    if name == "update":
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_machines",
            help="update this machine, then each enrolled member, one at a time",
        )
    if name in remote.CAPABLE:
        parser.add_argument(
            "machine", nargs="?", help="one registered machine; omit to use this machine"
        )
    elif name in ("configure", "uninstall"):
        parser.add_argument("machine", nargs="?", help=argparse.SUPPRESS)


def normalize(args, parser):
    if args.command == "machines" and args.action == "set-ssh":
        for old, name in (("legacy_name", "hostname"), ("legacy_target", "ssh_target")):
            value = getattr(args, old)
            if value is not None:
                if getattr(args, name) is not None:
                    parser.error("Do not combine a flagged value with its old positional form.")
                setattr(args, name, value)
    if args.command == "tools" and args.action == "set":
        import manager

        if not args.tool or args.tool.startswith("-") or not manager.exact_version(args.version):
            parser.error(
                "tools set needs TOOL --version VERSION with an exact version, such as 24.14.1"
            )
    if args.command == "tools" and args.action in ("set", "remove"):
        args.pin = args.tool + "@" + args.version if args.action == "set" else args.tool
    return args


# Each command group answers to its other number too. Help shows only the canonical name.
OTHER_FORMS = {
    "addon": "addons",
    "agent": "agents",
    "backup": "backups",
    "domains": "domain",
    "group": "groups",
    "log": "logs",
    "machine": "machines",
    "marketplace": "marketplaces",
    "secret": "secrets",
    "tool": "tools",
    "dotfile": "dotfiles",
}


def canonical(arguments):
    """Replace a command's other singular or plural form with its canonical name."""
    arguments = list(arguments)
    position = 1 if arguments[:1] == ["help"] else 0
    if len(arguments) > position and arguments[position] in OTHER_FORMS:
        arguments[position] = OTHER_FORMS[arguments[position]]
    return arguments


def successor(arguments):
    path = arguments[1:] if arguments[:1] == ["help"] else arguments
    if not path:
        return None
    renamed = {
        "rollback": "backups restore",
        "pins": "tools",
        "profile": "status",
    }
    if path[0] in renamed:
        return renamed[path[0]]
    if path[:2] == ["content", "show"]:
        return "content"
    return None


def help_arguments(arguments, commands):
    """Treat help as a command word, never as the value of a named flag."""
    arguments = list(arguments)
    if not arguments:
        return ["--help"]
    if arguments == ["--version"]:
        return ["version"]
    if arguments[0] == "help":
        path = arguments[1:]
        if not path or path in (["help"], ["-h"], ["--help"]):
            return ["--help"]
        if path[0] not in commands or len(path) > 2:
            raise ValueError("Help needs a command and, optionally, one of its actions.")
        if len(path) == 2 and path[1] not in GROUPS.get(path[0], {}):
            raise ValueError("Unknown action for " + path[0] + ". Run help " + path[0] + ".")
        return path + ["--help"]
    if arguments[0] in commands:
        depth = 1
        if len(arguments) > 1 and arguments[1] in GROUPS.get(arguments[0], {}):
            depth = 2
        if len(arguments) == depth + 1 and arguments[-1] == "help":
            return arguments[:-1] + ["--help"]
    return arguments


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
