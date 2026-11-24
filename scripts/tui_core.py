"""Expose existing command cores to the terminal interface. No Textual dependency.

Usage: python3 scripts/tui_core.py ACTION [--backup PATH] [--machine NAME].
Actions cover every CLI command. Each area and action is listed in tui_navigation.py.
The private channel carries screen data and confirmations. stdout carries human output.
"""

import argparse
import datetime
from pathlib import Path
import sys

import command
import registry
import socket
import remote
import style
import ui_channel
import updates
from tui_navigation import ACTIONS as COMMAND_ACTIONS
from run_account import commit
import dotfiles_library

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ("dashboard", *COMMAND_ACTIONS)


def dashboard(home):
    profile = command.profile(home)
    rows = dict(command.status_rows(home, profile))
    backup = rows["Last backup"]
    if backup != "none":
        try:
            backup = datetime.datetime.strptime(Path(backup).name[:15], "%Y%m%d-%H%M%S").isoformat(
                " "
            )
        except ValueError:
            pass
    return [
        ("Machine", registry.machine_name(home)),
        ("Domain", rows["Domain"]),
        ("Content", profile["content_repo"]),
        ("Commit", commit(dotfiles_library.active(home)) or "not recorded"),
        ("Fleet", (ROOT / "VERSION").read_text().strip()),
        ("Updates", updates.summary(home, profile).removeprefix("Updates: ")),
        ("Last backup", backup),
        ("Drift", rows["Drift"]),
    ]


def send_connections(home):
    ui_channel.send(
        {
            "kind": "connections",
            "items": [
                name
                for name, record in sorted(registry.load(home)["machines"].items())
                # Remote actions reach fleet members; a connection alone has no Fleet to answer.
                if record.get("ssh_target") and record["enrolled"]
            ],
        }
    )


def send_backups(home):
    ui_channel.send(
        {"kind": "backups", "items": [str(path) for path in reversed(command.records(home))]}
    )


def dispatch(action, backup=None, machine=None, selected=None, entry=None):
    home = Path.home()
    if action == "backup-delete-selected":
        import backup_cleanup

        command.profile(home)
        result = backup_cleanup.delete_selected(home, selected or [])
        send_backups(home)
        return result
    if machine:
        return command.main([action, machine])
    if action == "dashboard":
        ui_channel.send({"kind": "dashboard", "rows": dashboard(home)})
        return 0
    if action == "logs":
        import run_index
        import run_logs

        ui_channel.send(
            {
                "kind": "logs",
                "items": [
                    {"id": item["id"], "label": run_logs.label(item)}
                    for item in run_index.entries(home)
                ],
            }
        )
    if action == "backups":
        send_backups(home)
    if action in ("machines", "topology"):
        send_connections(home)
    if action == "configure":
        raise ValueError("Configure returns to the terminal wizard. Close the interface first.")
    from tui_actions import arguments

    args = arguments(action, home)
    if args is None:
        print("Cancelled. Nothing changed.")
        return 0
    if backup:
        args.extend(["--backup", backup])
    if entry:
        args.extend(["--name", entry])
    try:
        result = command.main(args)
    except SystemExit as error:
        result = error.code
    if action in ("add-connection", "name-connection", "machine-remove"):
        send_connections(home)
    if action == "uninstall":
        ui_channel.send({"kind": "uninstalled", "value": not (home / command.PROFILE).exists()})
    if action == "backup-delete-all":
        send_backups(home)
    return result


def main(argv=None):
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=ACTIONS, help="existing core action to run")
    # An interface opened before a manager update can still send the old argument.
    parser.add_argument("legacy_backup", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("--entry", help="one saved attempt name, only for log-show")
    parser.add_argument("--backup", help="saved folder for rollback; no other action accepts it")
    parser.add_argument(
        "--selected-backup",
        action="append",
        help="one backup name selected for deletion; repeat for more",
    )
    parser.add_argument(
        "--machine", help="saved remote machine; only status, check, diff, doctor, update"
    )
    args = parser.parse_args(argv)
    if args.legacy_backup is not None:
        if args.backup is not None:
            parser.error("do not combine --backup with an old positional backup")
        args.backup = args.legacy_backup
    if args.backup and args.action != "rollback":
        parser.error("only rollback accepts a backup")
    if args.machine and (args.action not in remote.CAPABLE or args.backup):
        parser.error("remote actions are status, check, diff, doctor, and update")
    if args.selected_backup and args.action != "backup-delete-selected":
        parser.error("only selected backup deletion accepts backup selections")
    if not ui_channel.active():
        parser.error("this internal bridge requires the terminal interface channel")
    if args.entry and args.action != "log-show":
        parser.error("only log-show accepts a run entry")
    return dispatch(args.action, args.backup, args.machine, args.selected_backup, args.entry)


if __name__ == "__main__":
    sys.exit(style.run_cli(main, ROOT))
