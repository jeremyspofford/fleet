"""Operate an enrolled machine through the shared manager and run renderer.

Usage: dotfleet COMMAND [ARGS]. Run dotfleet --help for commands.
Parameters and examples are defined by each subcommand's --help.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
"""

import argparse
from convert import content_manifest
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from common import LoggerStream, atomic, bash, read_toml, relative, run, toml
import manager
import enrollment
import registry
import remote
import connections
import topology
import command_arguments
import command_views
import owned_views
import style
import run_index
import updates
import store_migration
import ui_channel
from command_display import command_name, current_command
from installation import COMMAND, MANAGER
from render_run import render, render_view
from run_account import RunAccount, commit, profile_changes
import dotfiles_library

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ".fleet/profile.toml"
TOOL_UPDATES = "Tool updates"
CONTROLS = {
    registry.REGISTRY,
    updates.CACHE,
    PROFILE,
    ".config/chezmoi/chezmoi.toml",
    ".fleet/outputs.json",
    ".fleet/agents.json",
    ".fleet/last-run.json",
    ".fleet/install-stages.json",
    COMMAND,
}
DESCRIPTIONS = {
    "agents": (
        "Show or switch agent items on this machine.\n"
        "An agent is a coding assistant; the content's agents table declares its items.\n"
        "The exception list in the profile turns an item off or keeps a local value.\n"
        "Off removes Fleet's output here. Local keeps this machine's own value unmanaged."
    ),
    "addons": (
        "List optional manager extensions.\n"
        "A manifest declares an extension’s actions.\n"
        "A receipt records its owned outputs.\n"
        "No add-ons ship with Fleet."
    ),
    "backups": (
        "Create, list, restore, or delete saved copies.\n"
        "A backup is a saved copy of managed files and the profile.\n"
        "List shows dates, recorded results, sizes, and locations without changing files."
    ),
    "capture": (
        "Copy edited home files into content, commit them, and push.\n"
        "To capture means to copy an edit of a managed file back into content.\n"
        "This takes whole files from plain and Stow content, for every machine.\n"
        "A review and the typed word capture come before any commit.\n"
        "It pushes only to an origin that you can push to; otherwise the commit stays local.\n"
        "It refuses secrets, group marker changes, and conflicts with newer content."
    ),
    "check": (
        "Fetch new update results.\n"
        "Status reads saved results; check fetches new results.\n"
        "Fetch manager and content upstreams quietly, with a 10-second limit per fetch.\n"
        "An upstream is the remote branch tracked by a checkout, a local Git copy.\n"
        "Save both results only when both checks succeed.\n"
        "Ask Mise for the newest version of each selected tool, and save those results.\n"
        "A newer tool version counts as an available update."
    ),
    "configure": (
        "Reopen the enrollment wizard.\n"
        "Review source, domain, identity, tools, groups, and agents before applying choices."
    ),
    "content": (
        "Show the content checkout and Git state.\n"
        "A checkout is a local Git copy.\n"
        "Show its repository, commit, behind state, and short Git status without changing files."
    ),
    "diff": (
        "Show pending file differences.\n"
        "Print paths and unified patches from chezmoi.\n"
        "A patch shows removed (-) and added (+) lines.\n"
        "Change nothing."
    ),
    "doctor": (
        "Check this machine and name any fixes.\n"
        "Check profile completeness, managed file differences, clean checkouts, and backups.\n"
        "A profile stores machine choices; a clean checkout has no local edits."
    ),
    "dotfiles": (
        "Download dotfiles repositories and choose the active one.\n"
        "Each download is a Git clone in ~/.fleet/dotfiles.\n"
        "Only the active repository is applied, updated, checked, and compared.\n"
        "Downloading never activates a repository; use does, after a review."
    ),
    "domain": (
        "Show or choose content additions.\n"
        "A domain selects content additions.\n"
        "The content declares each domain; core always applies.\n"
        "A missing domain_chosen field means UNCHOSEN.\n"
        "Setting a named domain confirms the choice."
    ),
    "groups": (
        "Show or switch optional content.\n"
        "A group selects optional files or alias blocks.\n"
        "Turn a group on to include it, or off to remove its managed content."
    ),
    "logs": (
        "Show saved change-making attempts.\n"
        "The index keeps the newest 100 attempts, including failures before apply.\n"
        "Read-only commands do not add entries.\n"
        "Backup deletion removes its detailed run record; the index entry remains until pruned."
    ),
    "marketplaces": (
        "Manage agent packages through Fleet Marketplace.\n"
        "A marketplace is a Git repository with a fleet-marketplace.toml catalog.\n"
        "A package bundles skills, rule sets, servers, connectors, plugins, or hooks.\n"
        "Installed packages live in ~/.fleet/marketplace-packages; records stay in the manager store."
    ),
    "machines": (
        "Manage fleet members and saved connections.\n"
        "The registry, registry.toml, stores machine records.\n"
        "List shows fleet members only and makes no live network queries.\n"
        "Install deploys Fleet to another machine and registers both machines.\n"
        "Uninstall removes Fleet from another member after typed confirmation.\n"
        "Export shows this machine; remove deletes a stale record.\n"
        "SSH securely connects to another machine.\n"
        "Set-ssh saves connection data and tests SSH once.\n"
        "One name supplies both fields; without flags, pick a host in a terminal."
    ),
    "tools": (
        "Show or set exact tool versions.\n"
        "A selection records an exact tool version.\n"
        "Set installs that exact version.\n"
        "Remove clears the selection without uninstalling the tool.\n"
        "Uninstall removes one extra installed version after typed confirmation.\n"
        "Update records and installs the newest version that Mise finds.\n"
        "Adopt records the installed version of each tool without a recorded version."
    ),
    "secrets": (
        "List secret names as set or unset.\n"
        "The content manifest, the repository’s settings file, declares secret variable names.\n"
        "Set means a nonempty value exists.\n"
        "Never print values."
    ),
    "status": (
        "Show saved state without network requests.\n"
        "Status reads saved update results; check fetches new results.\n"
        "Show the profile, selected tools, groups, checkouts, drift, backups, and last update check.\n"
        "A profile stores machine choices; drift means managed files differ."
    ),
    "topology": (
        "Show this machine’s connection tree.\n"
        "Topology means connection layout.\n"
        "Show each recorded observation with its time.\n"
        "Check nothing live; another machine may have a different view."
    ),
    "tui": (
        "Open the full-screen terminal interface.\n"
        "Arrows and Tab move; Enter selects; Esc goes back; q quits.\n"
        "Configure returns to the existing wizard.\n"
        "If the interface environment is missing, print plain command alternatives instead."
    ),
    "uninstall": (
        "Remove Fleet and its managed files.\n"
        "Type uninstall to confirm; press Enter after each answer.\n"
        "Keep content and backups.\n"
        "Offer to restore the oldest backup."
    ),
    "update": (
        "Check updates and drift before applying.\n"
        "Compare manager and content with their upstreams, the remote branches they track.\n"
        "Drift means managed files differ.\n"
        "Current sources without drift stop without a backup or review.\n"
        "Each attempt keeps a small entry in logs.\n"
        "A failed check stops without applying files.\n"
        "Use --all to update this machine, then each enrolled member, one at a time.\n"
        "A failed or unreachable member never stops the rest; a summary names each result.\n"
        "Name one machine or use --all, never both."
    ),
    "version": ("Show manager and content versions.\nA commit identifies a saved Git version."),
}


class HelpFormatter(argparse.RawTextHelpFormatter):
    def add_argument(self, action):
        super().add_argument(action)
        # Older Python releases omit nested indentation when sizing command columns.
        if action.help is not argparse.SUPPRESS:
            for nested in self._iter_indented_subactions(action):
                width = len(self._format_action_invocation(nested)) + self._current_indent
                self._action_max_length = max(self._action_max_length, width)

    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "Usage: ")


def parser():
    display = current_command()
    argument_parser = style.ArgumentParser(
        prog=display,
        description="Manage enrolled machines.",
        formatter_class=HelpFormatter,
    )
    argument_parser.add_argument(
        "--version",
        action="version",
        version=(ROOT / "VERSION").read_text().strip(),
        help="show manager and content versions, then exit",
    )
    subcommands = argument_parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND", title="commands"
    )
    argument_parser._optionals.title = "global options"
    argument_parser._action_groups.remove(argument_parser._optionals)
    argument_parser._action_groups.append(argument_parser._optionals)
    for name, detail in DESCRIPTIONS.items():
        if name in remote.CAPABLE:
            detail += (
                "\nSSH securely connects to another machine."
                "\nAdd MACHINE to use one registered machine and its own saved choices."
                "\nRemote runs never prompt; a failed connection prints an error."
            )
        elif name in remote.PROFILE_ACTIONS:
            detail += (
                "\nSSH securely connects to another machine."
                "\nAdd --machine NAME to list or save a choice on one registered machine."
                "\nThat machine saves the choice; its next update applies it."
            )
        elif name in remote.REASONS:
            detail += "\nRemote use is refused: " + remote.REASONS[name] + "."
        epilog = command_arguments.help_epilog(name, display)
        command_parser = subcommands.add_parser(
            name,
            prog=display + " " + name,
            help=detail.splitlines()[0],
            description=detail,
            formatter_class=HelpFormatter,
            epilog=epilog,
        )
        if name in command_arguments.GROUPS:
            command_arguments.add_groups(command_parser, name, HelpFormatter, display)
        else:
            command_arguments.add_parameters(command_parser, name)
    return argument_parser


def profile(home):
    result = read_toml(home / PROFILE)
    if not result:
        raise ValueError("No profile found. Run the README one-line installer first.")
    return result


def records(home):
    return sorted(
        (
            path
            for path in (home / ".fleet/backups").glob("*")
            if (path / "MANIFEST.json").is_file()
        ),
        key=lambda path: (path.name[:15], int(path.name[16:] or "0")),
    )


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def status(home):
    """Return drift lines: chezmoi status, then agent outputs that differ from their records."""
    import agent_deploy

    output = run("chezmoi", "status").strip()
    agent_drift = agent_deploy.drift(home, read_toml(home / PROFILE))
    return "\n".join(filter(None, [output, *("agent " + line for line in agent_drift)]))


def info(title, pairs, home):
    print(render_view(title, pairs, str(home)), end="")


def checkout_state(path):
    sha = commit(path)
    if not sha:
        return f"missing; run {current_command()} update"
    # Unmanaged files, such as untracked notes, never count as content edits.
    dirty = bool(updates.managed_edits(path))
    ref = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "@{upstream}"],
        text=True,
        capture_output=True,
    )
    behind = "upstream unknown"
    if ref.returncode == 0:
        counts = run(
            "git",
            "-C",
            str(path),
            "rev-list",
            "--left-right",
            "--count",
            "HEAD..." + ref.stdout.strip(),
        ).split()
        behind = f"{counts[1]} behind last fetched upstream"
        ahead, late = int(counts[0]), int(counts[1])
        if ahead:
            # A capture that origin did not take leaves local commits. Name the next step.
            plural = "commit" if ahead == 1 else "commits"
            step = f"git -C {path} push origin HEAD"
            if late:
                step = f"git -C {path} pull --rebase, then {step}"
            behind += f" · {ahead} content {plural} not pushed; run {step}"
    return f"{sha[:8]} · {behind} · " + ("local edits" if dirty else "clean")


def pin_rows(saved_profile, home):
    installed = enrollment.installed(home)
    return [
        (name, f"{format_pin(request)} · installed: {enrollment.versions(installed.get(name, []))}")
        for name, request in saved_profile.get("pins", {}).items()
    ]


def format_pin(value):
    from render_run import human

    return human(value)


def domain_choices(home):
    from convert import content_domains

    manifest = content_manifest(dotfiles_library.active(home))
    descriptions = manifest.get("domains", {})
    return {
        name: descriptions.get(
            name,
            "Base files only."
            if name == "core"
            else "Content additions; inspect the overlay file.",
        )
        for name in content_domains(manifest)
    }


def command_profile_gaps(saved_profile):
    """Keep the manager's validation, with fixes users can run through this command."""
    display = command_name(saved_profile)
    gaps = []
    for gap in manager.profile_gaps(saved_profile):
        if gap.startswith("Domain "):
            gap = gap.replace(
                "Supply --domain.",
                f"Run {display} domain list, then {display} domain set --name NAME.",
            )
        elif gap.startswith(("Git name ", "Git email ")):
            gap = gap.split(". Supply ", 1)[0] + f". Run {display} configure."
        elif " tool version is floating " in gap:
            tool = gap.split(" tool version is floating ", 1)[0]
            gap = (
                gap.split(". Supply ", 1)[0]
                + f". Run {display} tools set {tool} --version VERSION with a full version."
            )
        gaps.append(gap)
    return gaps


def read_view(args, home, saved_profile):
    display = command_name(saved_profile)
    name = args.command
    manifest = content_manifest(dotfiles_library.active(home))
    if name == "content":
        pairs = owned_views.content_rows(home, saved_profile, checkout_state)
    elif name in (
        "agents",
        "backups",
        "secrets",
        "machines",
        "marketplaces",
        "addons",
        "domain",
        "groups",
    ):
        entities = command_views.collection(name, home, saved_profile, manifest)
        empty = {
            "backups": f"No backups yet. Run {display} backups create.",
            "secrets": "No secret variables declared.",
            "groups": "No content groups declared.",
            "agents": "No agent items declared.",
            "marketplaces": f"No marketplaces. Run {display} marketplaces add --source URL.",
            "addons": "No installed add-ons. None ship with Fleet.",
        }.get(name, "None recorded.")
        # The marketplaces view names the feature, Fleet Marketplace.
        title = {
            "marketplaces": "Fleet Marketplace",
            "backups": "Backups in ~/.fleet/backups",
        }.get(name, name.capitalize())
        rows = command_views.uses_rows(name, entities)
        command_views.directory(title, entities, home, empty, rows)
        if name == "domain":
            print(f"\nChoose: {display} domain set --name NAME · use a name listed above")
        if name == "agents" and entities:
            print(f"\nChange: {display} agents set --name ITEM --state on, off, or local")
        return 0
    elif name == "tools":
        print(command_views.tool_view(home, saved_profile, "No tool versions recorded."), end="")
        return 0
    elif name == "version":
        pairs = [
            ("Manager", (ROOT / "VERSION").read_text().strip()),
            ("Content commit", commit(dotfiles_library.active(home))),
        ]
    elif name == "doctor":
        gaps = command_profile_gaps(saved_profile)
        drift = status(home)
        pairs = [("Profile", "PASS" if not gaps else "FAIL · resolve the gaps below")]
        pairs.append(
            (
                "Domain choice",
                "PASS · chosen"
                if saved_profile.get("domain_chosen", False)
                else f"FAIL · UNCHOSEN; run {display} domain list",
            )
        )
        pairs += [(f"Gap {number}", gap) for number, gap in enumerate(gaps, 1)]
        pairs += [
            (
                "Managed files",
                f"FAIL · run {display} diff to inspect drift" if drift else "PASS · no drift",
            )
        ]
        if drift:
            # Name each file: a chezmoi status line ends with its path; agent lines name theirs.
            names = [
                line[3:] if not line.startswith("agent ") else line
                for line in drift.splitlines()
                if line.strip()
            ]
            pairs.append(
                (
                    "Drifted files",
                    ", ".join(name if name.startswith("agent ") else "~/" + name for name in names),
                )
            )
        for label, path in [
            ("Manager checkout", home / MANAGER),
            ("Content checkout", dotfiles_library.active(home)),
        ]:
            clean = (path / ".git").exists() and not (
                updates.managed_edits(path)
                if label == "Content checkout"
                else run("git", "-C", str(path), "status", "--porcelain").strip()
            )
            pairs.append(
                (
                    label,
                    "PASS · clean"
                    if clean
                    else f"FAIL · save local edits, then run {display} update",
                )
            )
        pairs.append(("Backup", "PASS" if records(home) else f"FAIL · run {display} backup"))
        config_path = home / ".config/chezmoi/chezmoi.toml"
        source = read_toml(config_path).get("sourceDir", "")
        pairs.append(("Manager config", str(config_path)))
        pairs.append(
            (
                "Converted source",
                "PASS · " + source
                if source and Path(source).is_dir()
                else f"FAIL · run {display} update",
            )
        )
        info("Doctor", pairs, home)
        return int(any("FAIL" in value for _, value in pairs))
    else:
        pairs = status_rows(home, saved_profile)
    print(
        render_view(
            name.capitalize(),
            pairs or [("Selections", "none")],
            str(home),
            inline_keys={key for key, _ in pairs if key.startswith("Tool ") and key != TOOL_UPDATES}
            if name == "status"
            else (),
        ),
        end="",
    )
    return 0


def status_rows(home, saved_profile):
    display = command_name(saved_profile)
    backups = records(home)
    last = next((path for path in reversed(backups) if (path / "RUN.json").exists()), None)
    record = load_json(last / "RUN.json", {}) if last else {}
    pairs = [
        (
            "Domain",
            saved_profile.get("domain", "core")
            + (
                " · chosen"
                if saved_profile.get("domain_chosen", False)
                else f" · UNCHOSEN; run {display} domain list"
            ),
        ),
        (
            "Identity",
            (saved_profile.get("git_name", "") + " <" + saved_profile.get("git_email", "") + ">")
            if saved_profile.get("git_name", "").strip()
            and saved_profile.get("git_email", "").strip()
            else f"missing; run {display} configure",
        ),
    ]
    pairs += [("Tool " + key, value) for key, value in pin_rows(saved_profile, home)] or [
        ("Tools", "none")
    ]
    pairs += [("Group " + key, value) for key, value in saved_profile.get("groups", {}).items()]
    pairs += [
        (
            "Agents",
            ", ".join(
                name + (" on" if enabled else " off")
                for name, enabled in sorted(saved_profile.get("agents", {}).items())
            )
            or "none",
        ),
        ("Profile file", str(home / PROFILE)),
        ("Edit choices", f"Use {display} domain set, tools set, groups set, or configure."),
        ("Content", checkout_state(dotfiles_library.active(home))),
        ("Updates", updates.summary(home, saved_profile).removeprefix("Updates: ")),
        *(
            [("Fleet Marketplace", marketplace_summary)]
            if (marketplace_summary := marketplaces_summary(home))
            else []
        ),
        *(
            [(TOOL_UPDATES, tool_summary)]
            if (tool_summary := tools_summary(home, saved_profile, display))
            else []
        ),
        ("Drift", f"present; run {display} diff" if status(home) else "none"),
        (
            "Last run",
            str(last) + " · " + str(record.get("result", "unknown")) if last else "none",
        ),
        ("Last backup", str(backups[-1]) if backups else "none"),
        ("Manager config", str(home / ".config/chezmoi/chezmoi.toml")),
        (
            "Converted source",
            read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", f"missing; run {display} update"
            ),
        ),
    ]
    return pairs


def tools_summary(home, saved_profile, display):
    import tool_updates

    return tool_updates.summary(home, saved_profile, display)


def marketplaces_summary(home):
    import marketplaces

    return marketplaces.summary(home)


def prepare_manager(home, workspace):
    return updates.stage(home / MANAGER, workspace / "manager")


def update(home):
    run_index.stage("check")
    saved_profile = profile(home)
    if updates.check(home, saved_profile, display=False) == 2:
        return 2
    try:
        drift = status(home)
    except (OSError, ValueError, RuntimeError) as error:
        run_index.failure("Warning: Could not check local drift: " + str(error))
        print("Warning: Could not check local drift: " + str(error), file=sys.stderr)
        return 2
    checked = load_json(home / updates.CACHE, {})
    triggers = [source["name"] + " update" for source in checked["sources"] if source["behind"]]
    if drift:
        triggers.append("drift repair")
    from run_account import RECORD

    # A remote choice saves the profile only. The last apply recorded the profile it used.
    applied = load_json(home / RECORD, {}).get("profile_sha256")
    if applied and applied != profile_digest(home):
        triggers.append("profile change")
    if not triggers:
        run_index.outcome("NO_CHANGE")
        print(updates.CURRENT_MESSAGE)
        return 0
    with tempfile.TemporaryDirectory(prefix="dotfleet-update-") as folder:
        run_index.stage("manager checkout")
        staged = prepare_manager(home, Path(folder))
        environment = dict(os.environ)
        environment.pop("DOTFLEET_LOCAL_CONTENT", None)
        environment["FLEET_UPDATE_TRIGGERS"] = json.dumps(triggers)
        arguments = [bash(), str(staged / "install.sh")]
        if ui_channel.active() or os.environ.get(remote.CONTEXT) == "1":
            # TUI and SSH runs leave profile decisions to a real terminal on the target.
            arguments.append("--defaults")
        run_index.stage("installer")
        return subprocess.call(arguments, env=environment, pass_fds=ui_channel.descriptors())


def preview(home, saved_profile, stat=False):
    # Fetch only into scratch. Tool queries and chezmoi state also stay there.
    with tempfile.TemporaryDirectory(prefix="dotfleet-diff-") as folder:
        workspace = Path(folder)
        staged = prepare_manager(home, workspace)
        # Use the updated manager's code, without applying it or installing tools.
        return subprocess.call(
            [
                sys.executable,
                str(staged / "scripts/command.py"),
                "_preview",
                *(["--stat"] if stat else []),
            ],
            env=dict(os.environ, DOTFLEET_PREVIEW="1"),
        )


def preview_current(home, saved_profile, stat=False):
    with tempfile.TemporaryDirectory(prefix="dotfleet-plan-") as folder:
        workspace = Path(folder)
        original = Path(os.environ.get("MISE_DATA_DIR", home / ".local/share/mise"))
        os.environ.setdefault("MISE_INSTALLS_DIR", str(original / "installs"))
        os.environ.setdefault("MISE_PLUGINS_DIR", str(original / "plugins"))
        for key in ("DATA", "CACHE", "STATE"):
            os.environ["MISE_" + key + "_DIR"] = str(workspace / key.lower())
        checkout = enrollment.stage_repo(home, saved_profile["content_repo"], workspace)
        _, manifest = manager.detect(checkout, saved_profile.get("format"))
        # Agent planning messages are progress, not differences.
        with contextlib.redirect_stdout(io.StringIO()):
            plan = manager.prepare(
                home, checkout, saved_profile, manifest, workspace / "plan", preview=True
            )
        from preview_diff import patches

        changes = patches(home, saved_profile, plan, workspace / "plan", ROOT)
        conflicts = plan["agents"].conflicts
        for line in conflicts:
            print("Agent conflict: " + line)
        if conflicts and not changes:
            return 1
        if not changes:
            print(
                f"No differences. Your files match {saved_profile['content_repo']} @ {commit(checkout)[:8]}."
            )
            return 0
        if isinstance(sys.stdout, LoggerStream):
            sys.stdout.diff = True
        for name, patch in changes:
            print("~/" + name)
            if not stat:
                print(patch, end="" if patch.endswith("\n") else "\n")
        return 1


def managed_paths(home):
    import agent_deploy

    paths = set(load_json(home / ".fleet/outputs.json", {})) | CONTROLS
    # Agent shared files are not managed paths. Only Fleet's entries inside them belong to Fleet.
    paths |= set(agent_deploy.load_inventory(home).get("paths", {}))
    if (home / ".config/chezmoi/chezmoi.toml").exists():
        paths |= set(
            filter(
                None,
                run(
                    "chezmoi",
                    "managed",
                    "--include",
                    "files,symlinks",
                    "--path-style",
                    "relative",
                    "-0",
                ).split("\0"),
            )
        )
    return paths


def confirm(word, explanation, record=None):
    if ui_channel.active():
        return ui_channel.review(record, word, explanation)
    if not manager.has_terminal():
        raise ValueError(
            f"This action needs a terminal. Type {word} there after reviewing the changes."
        )
    return (
        manager.ask(
            "Confirm",
            "cancel",
            [word, "cancel"],
            detail=manager.options(
                [(word, "Proceed with the change above."), ("cancel", "Stop. Nothing changes.")],
                "cancel",
                None,
                intro=explanation,
            ),
        )
        == word
    )


def validate_backup(home, selected):
    folder = Path(selected).expanduser()
    if not folder.is_absolute():
        folder = home / ".fleet/backups" / folder
    folder = folder.resolve()
    if folder.parent != (home / ".fleet/backups").resolve():
        raise ValueError("Select a backup directly inside ~/.fleet/backups.")
    entries = load_json(folder / "MANIFEST.json", None)
    if not isinstance(entries, list):
        raise ValueError("Backup MANIFEST.json is missing or invalid.")
    operations = []
    for item in entries:
        saved_name = str(relative(item["path"]))
        name = store_migration.relative_path(saved_name)
        if name == registry.REGISTRY:
            continue  # Other machines' records must not rewind during local restore.
        destination = home / name
        if not destination.parent.resolve().is_relative_to(home.resolve()):
            raise ValueError("Restore parent leaves home: " + name)
        if item["state"] not in ("saved", "absent"):
            raise ValueError("Invalid backup state: " + name)
        source = folder / "home" / saved_name if item["state"] == "saved" else None
        if source:
            if not source.parent.resolve().is_relative_to((folder / "home").resolve()):
                raise ValueError("Backup parent leaves backup: " + name)
            if manager.fingerprint(source) != item.get("fingerprint") or not (
                source.is_file() or source.is_symlink()
            ):
                raise ValueError("Backup file failed its recorded fingerprint: " + name)
        operations.append((name, source))
        if item.get("external"):
            target = Path(item["external"])
            external = folder / "external" / str(target).lstrip("/")
            if (
                not target.is_absolute()
                or ".." in target.parts
                or not external.is_file()
                or external.is_symlink()
            ):
                raise ValueError("Invalid external backup target: " + str(target))
            if not external.resolve().is_relative_to((folder / "external").resolve()):
                raise ValueError("External backup leaves its storage folder.")
            # Restore only the original linked destination, never an arbitrary path in edited metadata.
            if (
                not source
                or not source.is_symlink()
                or (destination.parent / os.readlink(source)).resolve() != target
            ):
                raise ValueError("External target does not match the saved symlink.")
            if manager.fingerprint(target) != manager.fingerprint(external):
                # The normal backup inventory follows current symlinks. Without this
                # link, it cannot capture the external file before replacement.
                if not destination.is_symlink() or destination.resolve() != target:
                    raise ValueError(
                        (
                            "External target changed and its original link is missing. "
                            "Preserve it before restoring: "
                        )
                        + str(target)
                    )
                operations.append((str(target), external))
    return folder, restore_shared_entries(home, folder, operations)


def scratch_folder():
    """Hold temporary rewritten files until this command exits."""
    import atexit

    folder = Path(tempfile.mkdtemp(prefix="dotfleet-shared-"))
    atexit.register(shutil.rmtree, folder, True)
    return folder


def restore_shared_entries(home, folder, operations):
    """Restore only Fleet's entries in agent shared files; keep values written after the backup."""
    import agent_deploy

    current = agent_deploy.load_inventory(home)
    saved_state = folder / "home" / agent_deploy.INVENTORY
    entries = list(current.get("entries", []))
    if saved_state.is_file():
        saved = json.loads(saved_state.read_text())
        if saved.get("schema_version") == agent_deploy.SCHEMA:
            entries += saved.get("entries", [])
    shared = {item["file"] for item in entries}
    if not shared:
        return operations
    inventory = {"entries": entries}
    workspace = scratch_folder()
    return [
        (name, agent_deploy.restore_shared(home, name, source, workspace, inventory))
        if name in shared
        else (name, source)
        for name, source in operations
    ]


def transaction(
    home,
    operations,
    label,
    selected=None,
    require_word=None,
    tree_removals=(),
    software=(),
    before_files=None,
):
    """Record the pre-change inventory, confirm, back up, and execute exact file operations.

    Software rows appear in the review. before_files runs after the backup, before any file change.
    """
    operations = [(name, source) for name, source in operations if name != registry.REGISTRY]
    backup_paths = sorted(
        {name for name, _ in operations if not Path(name).is_absolute()} | {registry.REGISTRY}
    )
    account = RunAccount(home, ROOT)
    account.checkouts = []
    account.software = list(software)
    folder = manager.backup_location(home)
    account.locations.update(
        {
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", "not configured"
            ),
            "Backup folder": str(folder),
        }
    )
    account.manifest = manager.backup_inventory(home, backup_paths)
    account.files = []
    for name, source in operations:
        path = home / name
        before = manager.fingerprint(path)
        after = manager.fingerprint(source) if source else None
        if before != after:
            account.files.append(
                {
                    "path": str(path),
                    "relative": name,
                    "before": before,
                    "action": "removed"
                    if after is None
                    else "added"
                    if before is None
                    else "changed",
                }
            )
    for path in tree_removals:
        if path.exists():
            account.files.append(
                {
                    "path": str(path),
                    "relative": str(path.relative_to(home)),
                    "before": None,
                    "action": "removed",
                }
            )
    new_profile = next(
        (read_toml(source) if source else {} for name, source in operations if name == PROFILE),
        account.old_profile,
    )
    account.profile = profile_changes(account.old_profile, new_profile)
    account.selected_profile = new_profile
    account.instruction_commands["after"] = command_name(new_profile)
    with tempfile.TemporaryDirectory(prefix="dotfleet-restore-") as temporary:
        account.diff_path = Path(temporary) / "diff"
        diff = (
            run("chezmoi", "--no-pager", "--use-builtin-diff", "--color=false", "diff")
            if (home / ".config/chezmoi/chezmoi.toml").exists()
            else ""
        )
        atomic(account.diff_path, diff)
        account.status = status(home) if (home / ".config/chezmoi/chezmoi.toml").exists() else ""
        for name, _ in operations:
            target = home / name
            if target.is_dir() and not target.is_symlink():
                raise ValueError("Refusing to replace a directory: " + str(target))
        run_index.stage("review")
        if require_word:
            print(render(account.record(), future=True), end="")
            if not confirm(require_word, label, account.record()):
                account.finish("CANCELLED")
                return 0
        if manager.backup_inventory(home, backup_paths) != account.manifest:
            raise ValueError("Files changed during review. Run the command again.")
        run_index.stage("backup")
        manager.backup(home, backup_paths, folder)
        account.bind_backup(folder)
        source = home / ".fleet/chezmoi-source"
        if source.is_dir():
            shutil.copytree(source, folder / "previous-chezmoi-source", symlinks=True)
        try:
            if before_files:
                before_files(account)
            # External target backups follow the original symlink inventory above.
            for name, saved in operations:
                target = home / name
                if target.exists() and target.is_dir() and not target.is_symlink():
                    raise ValueError("Refusing to replace a directory with a file: " + str(target))
                if saved is None:
                    target.unlink(missing_ok=True)
                elif saved.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.unlink(missing_ok=True)
                    target.symlink_to(os.readlink(saved))
                else:
                    atomic(target, saved.read_bytes(), saved.stat().st_mode & 0o777)
            if selected and (selected / "previous-chezmoi-source").is_dir():
                if source.exists():
                    shutil.rmtree(source)
                shutil.copytree(selected / "previous-chezmoi-source", source, symlinks=True)
            # Logging must survive removing the running manager checkout.
            if tree_removals:
                log_root = Path(temporary) / "logger"
                (log_root / "scripts").mkdir(parents=True)
                shutil.copy2(ROOT / "scripts/logger.sh", log_root / "scripts/logger.sh")
                shutil.copy2(ROOT / "VERSION", log_root / "VERSION")
                for stream in (sys.stdout, sys.stderr):
                    if isinstance(stream, LoggerStream):
                        stream.root = log_root
            for path in tree_removals:
                if path.exists():
                    path.rename(folder / ("removed-" + path.name))
                    account.applied_files.add(str(path))
            store_migration.repair_config(home)
            account.profile_written = True
            account.confirm_files(manager.fingerprint)
            drift = status(home) if (home / ".config/chezmoi/chezmoi.toml").exists() else ""
            return account.finish(
                "SUCCESS",
                [],
                f"differences remain; run {current_command(home)} diff" if drift else "clean",
            )
        except (OSError, ValueError, RuntimeError) as error:
            account.confirm_files(manager.fingerprint)
            account.finish("FAILED", [str(error)])
            return 1


def restore_command(args, home):
    backups = records(home)
    command_views.directory(
        "Backups in ~/.fleet/backups",
        owned_views.backup_entities(home, backups),
        home,
        "No backups yet.",
        rows=True,
    )
    if not args.backup:
        if not backups:
            return 0
        if ui_channel.active():
            from tui_actions import choose

            chosen = choose(
                "Restore a backup",
                "Backup folder",
                [(path.name, str(path)) for path in reversed(backups)],
            )
        elif manager.has_terminal():
            chosen = manager.ask(
                "Backup",
                "cancel",
                detail=(
                    "Type a backup folder name from the list above to restore it.\n"
                    "  cancel  Stop. Nothing is restored.\n"
                    "Press Enter alone to choose cancel."
                ),
            )
        else:
            return 0
        if chosen in (None, "cancel"):
            return 0
    else:
        chosen = args.backup
    folder, operations = validate_backup(home, chosen)
    return transaction(home, operations, "Restore the listed files and profile.", folder, "restore")


def main(argv=None):
    home = Path.home()
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    if argv == ["_remote"]:
        return remote.receive()
    if argv in (["_preview"], ["_preview", "--stat"]) and os.environ.get("DOTFLEET_PREVIEW") == "1":
        try:
            return preview_current(home, profile(home), "--stat" in argv)
        except (OSError, ValueError, RuntimeError) as error:
            print("Preview failed: " + str(error), file=sys.stderr)
            return 2
    argument_parser = parser()
    original_arguments = command_arguments.canonical(sys.argv[1:] if argv is None else argv)
    successor = command_arguments.successor(original_arguments)
    if successor:
        print("Error: Use " + current_command(home) + " " + successor + ".", file=sys.stderr)
        return 2
    try:
        argv = command_arguments.help_arguments(original_arguments, DESCRIPTIONS)
    except ValueError as error:
        argument_parser.error(str(error))
    args = command_arguments.normalize(argument_parser.parse_args(argv), argument_parser)
    if args.command in command_arguments.GROUPS and args.action is None:
        args.help_parser.print_help()
        return 0
    if args.command == "version":
        return read_view(args, home, {})
    if getattr(args, "all_machines", False):
        if args.machine:
            argument_parser.error("Name one machine or use --all, never both.")
        return update_all(home, args, argument_parser)
    if getattr(args, "machine", None) and args.command in remote.PROFILE_ACTIONS:
        return remote_profile(home, args)
    if getattr(args, "machine", None):
        return remote.execute(
            home,
            args.machine,
            args.command,
            check=getattr(args, "check", False),
            stat=getattr(args, "stat", False),
        )
    if args.command == "tui":
        from tui_environment import launch

        return launch(home, ROOT)
    if args.command == "topology":
        view = topology.view(home)
        if args.json:
            stream = getattr(sys.stdout, "stream", sys.stdout)
            stream.write(json.dumps(view, indent=2) + "\n")
            stream.flush()
        else:
            print(topology.render(view), end="")
        return 0
    if args.command == "logs":
        import run_logs

        if args.action == "list" and args.limit < 1:
            argument_parser.error("--limit needs a positive count")
        return run_logs.show_command(home, args)
    if changes_state(args):
        label = args.command + (" " + args.action if getattr(args, "action", None) else "")
        return run_index.run(home, label, lambda: dispatch(args, argument_parser, home))
    return dispatch(args, argument_parser, home)


def update_all(home, args, argument_parser):
    """Update this machine, then each enrolled member in turn. One failure never stops the rest.

    Print one result line as each machine finishes, then a summary. Exit with the worst code.
    """
    this = registry.machine_name(home)
    members = sorted(
        name
        for name, record in registry.load(home)["machines"].items()
        if record.get("enrolled") and name != this
    )
    total = len(members) + 1
    code = run_index.run(home, "update", lambda: dispatch(args, argument_parser, home))
    results = [(this, code, "update succeeded" if code == 0 else f"update failed (exit {code})")]
    print(f"[1/{total}] {this}: {results[0][2]}.")
    machines = registry.load(home)["machines"]
    for number, name in enumerate(members, 2):
        report = {}
        member_code = remote.execute(home, name, "update", report=report)
        result = report.get("result", f"exit {member_code}")
        if connections.is_sibling(machines[name].get("ssh_target")):
            result += " · sibling dispatch through wsl.exe"
        results.append((name, member_code, result))
        print(f"[{number}/{total}] {name}: {results[-1][2]}.")
    info(
        "Fleet update · " + str(total) + (" machine" if total == 1 else " machines"),
        [(name, result) for name, _, result in results],
        home,
    )
    return max(member_code for _, member_code, _ in results)


def dotfiles_command(home, args, display):
    """Download, list, activate, or remove dotfiles repositories in ~/.fleet/dotfiles."""
    library = dotfiles_library
    if args.action == "download":
        name = library.download(home, args.url, args.name, manager.has_terminal())
        print(
            f"Downloaded {name} into ~/{library.LIBRARY}/{name}. It is not active."
            f" Run {display} dotfiles use {name} to activate it."
        )
        return 0
    if args.action == "list":
        current = library.active_name(home)
        rows = [
            (
                name,
                library.source(library.path(home, name)) + (" · active" if name == current else ""),
            )
            for name in library.names(home)
        ]
        info("Dotfiles", rows or [("Downloads", "none")], home)
        return 0
    target = library.path(home, args.name)
    if not target.is_dir():
        raise ValueError(f"No download named {args.name}. Run {display} dotfiles list.")
    if args.action == "remove":
        if args.name == library.active_name(home):
            raise ValueError(
                f"{args.name} is the active repository. Use another repository first,"
                f" then remove {args.name}."
            )
        if not confirm(
            "remove",
            f"Delete ~/{library.LIBRARY}/{args.name} and its local Git history."
            " Applied files do not change.",
        ):
            print("Cancelled. Nothing removed.")
            return 0
        library.remove(home, args.name)
        print(f"Removed {args.name}.")
        return 0
    if args.name == library.active_name(home):
        print(f"{args.name} is already active. Nothing changed.")
        return 0
    origin = library.source(target)
    repo = str(target) if origin == "local" else origin
    # Activate exactly the downloaded commit; a later update fetches newer ones.
    os.environ["DOTFLEET_LOCAL_CONTENT"] = "1"
    return manager.main(["--defaults", "--repo", repo, "--dotfiles", args.name], switch="use")


def remote_profile(home, args):
    """Send one profile choice to a registered machine. Its manager validates and saves it."""
    names = remote.PROFILE_ACTIONS[args.command][args.action]
    values = {"tool": getattr(args, "tool", None), "version": getattr(args, "version", None)}
    values.update(name=getattr(args, "name", None), state=getattr(args, "state", None))
    try:
        request = remote.profile_request(
            args.command,
            args.action,
            {key: values[key] for key in names if values[key] is not None},
        )
    except ValueError as error:
        print("Error: " + str(error), file=sys.stderr)
        return 2
    return remote.execute(home, args.machine, args.command + " " + args.action, request=request)


def save_remote_choice(home, request):
    """Save one choice in this machine's profile without applying it.

    The update gate sees the saved change and applies it on the next update.
    """
    import copy

    from convert import require_domain

    saved = profile(home)
    display = command_name(saved)
    command, action, values = request["command"], request["action"], request["arguments"]
    changed = copy.deepcopy(saved)
    failed = 0
    manifest = content_manifest(dotfiles_library.active(home))
    if command == "domain":
        require_domain(manifest, values["name"], display)
        changed.update(domain=values["name"], domain_chosen=True)
    elif command == "groups":
        if values["name"] not in manifest.get("groups", {}):
            raise ValueError(f"Unknown group {values['name']}. Run {display} groups list.")
        changed.setdefault("groups", {})[values["name"]] = values["state"] == "on"
    elif command == "agents":
        name, state = values["name"], values["state"]
        if name not in agent_items(home):
            raise ValueError(f"Unknown agent item {name}. Run {display} agents list.")
        if name.startswith("skills.") and state == "local":
            raise ValueError("Skills are content files; choose on or off for a skill.")
        exceptions = changed.setdefault("agent_items", {})
        if state == "on":
            exceptions.pop(name, None)
        else:
            exceptions[name] = state
        if not exceptions:
            changed.pop("agent_items")
    elif action == "update":
        failed = save_newest_tools(home, saved, changed, values.get("tool"), display)
    elif action == "adopt":
        import tool_updates

        adopted, skipped = tool_updates.adoption(
            enrollment.installed(home), saved, display, values.get("tool")
        )
        print(adoption_report(adopted, skipped))
        record_adopted(changed, adopted)
    elif action == "set":
        tool, version = values["tool"], values["version"]
        if not manager.exact_version(version):
            raise ValueError("tools set needs an exact version, such as 24.14.1.")
        installed = enrollment.installed(home).get(tool, [])
        if not any(item.get("version") == version for item in installed):
            # Installing software is not an apply; the next update selects the version.
            run("mise", "install", tool + "@" + version)
        changed.setdefault("pins", {})[tool] = version
        unpinned = set(changed.get("unpinned_tools", [])) - {tool}
        if unpinned or "unpinned_tools" in changed:
            changed["unpinned_tools"] = sorted(unpinned)
    else:
        changed.setdefault("pins", {}).pop(values["tool"], None)
        changed["unpinned_tools"] = sorted(
            set(changed.get("unpinned_tools", [])) | {values["tool"]}
        )
    if changed == saved:
        if action not in ("update", "adopt"):
            print("No change. The profile already holds this choice.")
        return failed
    with tempfile.TemporaryDirectory(prefix="dotfleet-profile-") as folder:
        source = Path(folder) / "profile.toml"
        atomic(source, toml(changed))
        result = transaction(home, [(PROFILE, source)], "Save the profile choice.")
    if result == 0:
        print(f"Saved. The next update applies this change: run {display} update.")
    return result or failed


def save_newest_tools(home, saved, changed, tool, display):
    """Install each newer version now, and record it in the changed profile only.

    The caller saves the profile. Return 1 when any tool failed.
    """
    import tool_updates

    def choose(name, version):
        installed = enrollment.installed(home).get(name, [])
        if not any(item.get("version") == version for item in installed):
            # Installing software is not an apply; the next update selects the version.
            run("mise", "install", name + "@" + version)
        previous = changed["pins"][name]
        changed["pins"][name] = (
            dict(previous, version=version) if isinstance(previous, dict) else version
        )
        return "updated", ""

    return tool_updates.update(home, saved, tool, display, choose, remote=True)


def profile_digest(home):
    import hashlib

    path = home / PROFILE
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def changes_state(args):
    if args.command in ("update", "configure", "uninstall"):
        return True
    action = getattr(args, "action", None)
    if args.command == "machines" and action == "export":
        return bool(args.output)
    return action in (
        "add",
        "install",
        "set",
        "remove",
        "set-ssh",
        "create",
        "restore",
        "delete",
        "uninstall",
        "update",
        "adopt",
        "download",
        "use",
    )


def dispatch(args, argument_parser, home):
    try:
        run_index.stage("profile")
        saved_profile = profile(home)
        display = command_name(saved_profile)
        if args.command == "check":
            # An enrollment from before saved names keeps its name; check records its facts.
            registry.backfill(home)
            return updates.check(home, saved_profile, marketplaces=True, tools=True)
        if args.command == "backups" and args.action == "delete":
            import backup_cleanup

            if args.keep is not None and args.keep < 0:
                argument_parser.error("--keep needs zero or a positive count")
            names = backup_cleanup.select(home, args.name, args.keep, args.all_backups)
            return backup_cleanup.delete_selected(home, names)
        if args.command == "machines":
            if args.action == "set-ssh":
                name, target = args.hostname, args.ssh_target
                if name is None and target is None:
                    target = connections.pick(home)
                    if not target:
                        print("Cancelled. No connection saved.")
                        return 0
                return set_machine_ssh(home, name or target, target or name)
            if args.action == "remove":
                return remove_machine(home, args.hostname)
            if args.action == "install":
                return install_machine(home, args)
            if args.action == "uninstall":
                return uninstall_machine(home, args.name)
            if args.action == "export":
                info(
                    "Machine export",
                    owned_views.export_rows(
                        home, (ROOT / "VERSION").read_text().strip(), args.output
                    ),
                    home,
                )
                return 0
        if args.command == "agents" and args.action == "import":
            import agent_import

            print(agent_import.run(home, args.agent), end="")
            return 0
        if args.command == "dotfiles":
            return dotfiles_command(home, args, display)
        if args.command == "capture":
            import capture

            return capture.run(home, args.file, display, lambda: update(home), confirm)
        if args.command in ("status", "doctor", "content") or getattr(args, "action", None) in (
            "list",
            "show",
        ):
            return read_view(args, home, saved_profile)
        if args.command == "update":
            return update(home)
        if args.command == "diff":
            return preview(home, saved_profile, args.stat)
        if args.command == "configure":
            if not manager.has_terminal():
                raise ValueError(
                    f"Configure needs a terminal. Run {display} configure in an interactive shell."
                )
            return manager.main(["--configure"])
        if args.command == "backups" and args.action == "restore":
            if args.backup in registry.load(home)["machines"]:
                return remote.refusal(args.backup, "rollback")
            return restore_command(args, home)
        if args.command == "backups" and args.action == "create":
            import agent_deploy

            operations = [
                (
                    name,
                    home / name if (home / name).exists() or (home / name).is_symlink() else None,
                )
                for name in sorted(managed_paths(home) | agent_deploy.shared_files(home))
            ]
            # Snapshot only: transaction has no replacement operations or profile changes.
            return snapshot(home, operations)
        if args.command == "uninstall":
            if not manager.has_terminal() and not ui_channel.active():
                raise ValueError("Uninstall needs a terminal and typed confirmation.")
            backups = records(home)
            selected, restored = None, []
            restore = "keep"
            if backups and ui_channel.active():
                from tui_actions import choose

                restore = choose(
                    "After removing Fleet",
                    "Oldest backup",
                    [
                        ("keep — remove managed files; keep the backups", "keep"),
                        ("restore — restore the oldest saved files", "restore"),
                    ],
                    "keep",
                )
                if restore is None:
                    print("Cancelled. Nothing removed.")
                    return 0
            elif backups:
                restore = manager.ask(
                    "Oldest backup",
                    "keep",
                    ["restore", "keep"],
                    detail=manager.options(
                        [
                            (
                                "restore",
                                "Remove managed files, then restore the oldest saved files.",
                            ),
                            ("keep", "Remove managed files only."),
                        ],
                        "keep",
                        None,
                        intro="Your content and backups stay either way.",
                    ),
                )
            if restore == "restore":
                selected, restored = validate_backup(home, str(backups[0]))
            return remove_fleet(home, selected, restored, "uninstall")
        os.environ["DOTFLEET_LOCAL_CONTENT"] = "1"
        if args.command == "domain":
            from convert import require_domain

            require_domain(content_manifest(dotfiles_library.active(home)), args.name, display)
            return manager.main(["--defaults", "--domain", args.name])
        if args.command == "groups":
            return manager.main(["--defaults", "--group", args.name + "=" + args.state])
        if args.command == "agents":
            return set_agent_item(home, args.name, args.state, display)
        if args.command == "marketplaces":
            return change_marketplace(home, args, display)
        if args.command == "tools":
            if args.action == "uninstall":
                from tool_actions import uninstall

                return uninstall(home, saved_profile, args.tool, args.version)
            if args.action == "update":
                return update_tools(home, saved_profile, args.tool, display)
            if args.action == "adopt":
                return adopt_tools(home, saved_profile, args.tool, display)
            if not args.pin:
                argument_parser.error("tools set needs TOOL and --version; tools remove needs TOOL")
            if args.action == "remove":

                def unpin(value):
                    value.setdefault("pins", {}).pop(args.pin, None)
                    value["unpinned_tools"] = sorted(
                        set(value.get("unpinned_tools", [])) | {args.pin}
                    )

                return manager.main(["--defaults"], edit=unpin)
            tool, separator, version = args.pin.rpartition("@")
            if (
                not separator
                or not tool
                or not manager.exact_version(version)
                or tool.startswith("-")
            ):
                argument_parser.error(
                    "tools set needs TOOL --version VERSION with an exact version, such as 24.14.1"
                )
            return set_tool(home, tool, version)
    except (OSError, ValueError, RuntimeError) as error:
        if args.command == "check":
            print("Warning: Could not check updates: " + str(error), file=sys.stderr)
            return 2
        style.record_failure()
        run_index.failure("Error: " + str(error))
        print("Error: " + str(error), file=sys.stderr)
        return 2 if args.command == "diff" else 1
    return 0


def set_tool(home, tool, version, old=None):
    """Record one exact version, and install it after the review and backup."""
    installed = enrollment.installed(home).get(tool, [])
    present = next((item for item in installed if item.get("version") == version), None)
    location = (
        str(present.get("install_path", "Mise installation"))
        if present
        else str(home / ".local/share/mise/installs" / tool / version)
    )
    software = [
        {
            "name": tool,
            "version": version,
            "old": old,
            "path": location,
            "action": "present" if present else "installed",
        }
    ]

    def install(account):
        if not present:
            run("mise", "install", tool + "@" + version)
            actual = next(
                item
                for item in enrollment.installed(home).get(tool, [])
                if item.get("version") == version
            )
            software[0]["path"] = actual.get("install_path", location)
            account.applied_software.add(tool)
            account.save()

    return manager.main(
        ["--defaults", "--pin", tool + "=" + version],
        software=software,
        before_apply=install,
    )


def update_tools(home, saved_profile, tool, display):
    """Record and install each selected tool's newest version, one tools set apply per tool."""
    import tool_updates

    def apply(name, version):
        old = tool_updates.recorded(saved_profile["pins"][name])
        code = set_tool(home, name, version, old)
        if code:
            return "failed", f"the apply stopped with exit {code}; run {display} logs show"
        saved = tool_updates.recorded(profile(home).get("pins", {}).get(name))
        return ("updated", "") if saved == version else ("cancelled", "")

    return tool_updates.update(home, saved_profile, tool, display, apply)


def adoption_report(adopted, skipped):
    """Show the adopted and skipped sections, then the count line."""
    import tool_updates

    text = tool_updates.adoption_text(adopted, skipped)
    if not text:
        text = "Nothing to adopt. Every installed tool records a version.\n"
    return text + "\n" + tool_updates.adoption_count(adopted, skipped)


def record_adopted(value, adopted):
    """Record each adopted version. A tool removed earlier leaves the removed list."""
    if not adopted:
        return
    value.setdefault("pins", {}).update(adopted)
    if "unpinned_tools" in value:
        value["unpinned_tools"] = sorted(set(value["unpinned_tools"]) - set(adopted))


def adopt_tools(home, saved_profile, tool, display):
    """Record installed versions in one apply. Adopt installs and uninstalls nothing."""
    import tool_updates

    installed = enrollment.installed(home)
    adopted, skipped = tool_updates.adoption(installed, saved_profile, display, tool)
    if not adopted:
        print(adoption_report(adopted, skipped))
        return 0
    software = [
        {
            "name": name,
            "version": version,
            "old": None,
            "path": next(
                str(item.get("install_path", "Mise installation"))
                for item in installed[name]
                if item.get("version") == version
            ),
            "action": "present",
        }
        for name, version in adopted.items()
    ]
    code = manager.main(
        ["--defaults"], edit=lambda value: record_adopted(value, adopted), software=software
    )
    pins = profile(home).get("pins", {})
    # The result follows the apply review. A cancelled or failed apply records nothing.
    saved = {name: version for name, version in adopted.items() if pins.get(name) == version}
    print()
    print(adoption_report(saved, skipped))
    return code


def agent_items(home):
    """Return validated items plus skill folders, keyed by KIND.NAME."""
    import agent_table

    checkout = dotfiles_library.active(home)
    table = agent_table.load(checkout, content_manifest(checkout), enrollment.agent_manifest())
    import marketplaces

    items = {item.key: item for item in table.items}
    for skill in agent_table.skills(checkout):
        items.setdefault("skills." + skill, agent_table.Item("skills", skill, {}))
    # Pulled-in items follow content items; their names never collide with content names.
    items.update(marketplaces.items(home, enrollment.agent_manifest()))
    return items


def set_agent_item(home, name, state, display):
    items = agent_items(home)
    if name not in items:
        raise ValueError(f"Unknown agent item {name}. Run {display} agents list to see items.")
    if name.startswith("skills.") and state == "local":
        raise ValueError("Skills are content files; choose on or off for a skill.")

    def edit(value):
        exceptions = value.setdefault("agent_items", {})
        if state == "on":
            exceptions.pop(name, None)
        else:
            exceptions[name] = state
        if not exceptions:
            value.pop("agent_items")

    return manager.main(["--defaults"], edit=edit)


def comma_list(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def change_marketplace(home, args, display):
    """Stage a marketplace change, then render it through the shared review and apply."""
    import marketplaces

    saved = marketplaces.load_records(home)["marketplaces"]
    with tempfile.TemporaryDirectory(prefix="dotfleet-marketplace-") as workspace:
        if args.action == "update":
            return update_marketplaces(home, args, display, saved, Path(workspace))
        if args.action == "delete":
            if args.name not in saved:
                raise ValueError(
                    f"No marketplace named {args.name}. Run {display} marketplaces list."
                )
            marketplaces.PENDING[args.name] = None
            return manager.main(["--defaults"])
        name = args.name or marketplaces.default_name(args.source)
        if not marketplaces.NAME.fullmatch(name):
            raise ValueError("Marketplace names use lowercase letters, digits, and hyphens.")
        if name in saved:
            raise ValueError(f"Marketplace {name} exists. Choose another --name.")
        staged, commit = marketplaces.stage(home, args.source, workspace, args.pin or "")
        catalog = marketplaces.read_catalog(staged)
        take = marketplaces.take_from(comma_list(args.packages), comma_list(args.features))
        marketplaces.selection(catalog, take)
        marketplaces.PENDING[name] = {
            "source": args.source,
            "pin": args.pin or "",
            "commit": commit,
            "version": marketplaces.version(staged),
            "take": take,
            "staged": str(staged),
        }
        return manager.main(["--defaults"])


def update_marketplaces(home, args, display, saved, workspace):
    """Stage newer commits, pins, and selections. A pin blocks moving that marketplace."""
    import marketplaces

    if args.name and args.name not in saved:
        raise ValueError(f"No marketplace named {args.name}. Run {display} marketplaces list.")
    if not args.name and (
        args.pin or args.unpin or args.packages or args.features or args.take_all
    ):
        raise ValueError("A pin or selection change needs --name.")
    for name in [args.name] if args.name else sorted(saved):
        record = dict(saved[name])
        pin = args.pin if args.pin else "" if args.unpin else record.get("pin", "")
        staged, commit = marketplaces.stage(home, record["source"], workspace / name, pin)
        catalog = marketplaces.read_catalog(staged)
        take = record["take"]
        if args.take_all:
            take = {"all": True}
        elif args.packages or args.features:
            take = marketplaces.take_from(comma_list(args.packages), comma_list(args.features))
        marketplaces.selection(catalog, take)
        if record.get("pin") and pin == record["pin"] and not args.pin:
            print(f"Marketplace {name}: pinned at {pin}; the pin blocks updates.")
        if (commit, take, pin) == (record["commit"], record["take"], record.get("pin", "")):
            continue
        record.update(
            commit=commit,
            take=take,
            pin=pin,
            version=marketplaces.version(staged),
            staged=str(staged),
        )
        marketplaces.PENDING[name] = record
    if not marketplaces.PENDING:
        print("Marketplaces are current. Nothing to apply.")
        return 0
    return manager.main(["--defaults"])


def remove_machine(home, hostname):
    if hostname == registry.machine_name(home):
        raise ValueError(
            "This machine is active. Only another machine’s stale record can be removed."
        )
    if not (home / registry.REGISTRY).exists():
        raise ValueError("No registry exists. Nothing was removed.")

    def remove(data):
        key = registry.resolve(data, hostname)
        if key is None:
            raise ValueError("No saved machine named " + hostname + ". Nothing was removed.")
        own_id = registry.machine_id(home)
        if (
            key == registry.machine_name(home)
            or own_id
            and (data["machines"][key].get("machine_id") == own_id)
        ):
            raise ValueError(
                "This machine is active. Only another machine’s stale record can be removed."
            )
        registry.mark_removed(data, key)

    return change_registry(home, remove)


def remove_fleet(home, selected=None, restored=(), require_word="uninstall"):
    """Remove the manager and managed files under the uninstall rules. Content and backups stay.

    A remote unenroll passes no word: the caller already typed the confirmation.
    """
    import agent_deploy

    operations = dict.fromkeys(managed_paths(home))
    # Remove Fleet's entries from agent shared files; never delete those files.
    _, shared = agent_deploy.removal_operations(home, scratch_folder())
    operations.update(shared)
    operations.update(dict(restored))
    # Fleet uninstalls the plugins it installed. Plugins it never recorded stay the user's.
    # Restoring a backup at uninstall leaves plugins alone until the owner rules on it.
    plugins = (
        [] if selected else agent_deploy.uninstall_plugins_plan(home, read_toml(home / PROFILE))
    )
    rows = agent_deploy.plugin_software(agent_deploy.Plan(plugin_removals=plugins))
    return transaction(
        home,
        sorted(operations.items()),
        "Remove the manager and listed managed files. Content and backups stay.",
        selected,
        require_word,
        [
            home / MANAGER,
            home / ".fleet/chezmoi-source",
            home / ".fleet/tui",
            home / ".fleet/agents",
        ],
        software=rows,
        before_files=lambda account: agent_deploy.uninstall_plugins(
            plugins, enrollment.agent_manifest(), account
        ),
    )


def uninstall_machine(home, name):
    """Confirm here, dispatch an unenroll to the member, then mark it removed."""
    loaded = registry.load(home)
    data = loaded["machines"]
    name = registry.resolve(loaded, name) or name
    record = data.get(name)
    if name == registry.machine_name(home):
        raise ValueError(
            f"Run {current_command(home)} uninstall to remove Fleet from this machine."
        )
    if not record or not record["enrolled"]:
        raise ValueError(
            f"{name} is not a fleet member. Run {current_command(home)} machines list."
        )
    if not record.get("ssh_target"):
        raise ValueError(
            f"{name} has no saved connection. Run machines set-ssh --name {name} first."
        )
    explanation = (
        f"Remove Fleet from {name}: its manager and managed files go; its content, backups, and"
        " own files stay. Then remove it from this fleet."
    )
    if not confirm("uninstall", explanation):
        print("Cancelled. Nothing removed.")
        return 0
    code = remote.unenroll(home, name, record["ssh_target"])
    if code:
        return code

    def mark(data):
        registry.mark_removed(data, name)

    result = change_registry(home, mark)
    print(f"{name} left the fleet. Peers drop it at their next contact.")
    return result


def install_machine(home, args):
    """Resolve NAME to an SSH target or a sibling WSL distro, then run the deploy steps."""
    import deploy

    if wsl_install(home, args):
        return deploy.install(home, args)

    data = registry.load(home)["machines"]
    if args.name is None and args.ssh_target is None:
        chosen = connections.pick(home, purpose="install")
        if not chosen:
            print("Cancelled. Nothing was deployed.")
            return 0
        args.name = chosen
    args.name = args.name or args.ssh_target
    # NAME is a target. The registry may know it as a member's name or saved SSH target.
    member = registry.resolve(registry.load(home), args.name)
    record = data.get(member, {}) if member else {}
    if record.get("enrolled") and member != registry.machine_name(home):
        raise ValueError(
            f"{args.name} is already a fleet member. Run {current_command(home)} update {member}."
        )
    if args.name == registry.machine_name(home):
        raise ValueError("This machine is already enrolled. Choose another machine.")
    supplied = args.ssh_target is not None
    args.ssh_target = args.ssh_target or record.get("ssh_target") or args.name
    remote.validate_target(args.name)
    remote.validate_target(args.ssh_target)
    if args.return_target:
        remote.validate_target(args.return_target)
    if not supplied and not record and args.name not in connections.hosts(home):
        raise ValueError(f"{args.name} is not a host in your SSH config. Add --target user@host.")
    return deploy.install(home, args)


def wsl_install(home, args):
    """Choose the local WSL path when NAME names a distro on this host. Never guess against SSH.

    Return True when the deploy goes through wsl.exe. Then args holds the wsl: target.
    """
    import siblings

    name = args.name or ""
    wanted = args.ssh_target or ""
    via = getattr(args, "via", None)
    explicit = connections.is_sibling(wanted)
    if via == "ssh" or wanted and not explicit and via != "wsl":
        # A bare --target keeps its SSH meaning. It names a distro only as wsl:DISTRO or with --via wsl.
        return False
    data = registry.load(home)
    member = registry.resolve(data, name) if name else None
    recorded = data["machines"].get(member, {}) if member else {}
    saved = recorded.get("ssh_target", "")
    available = siblings.distros() if siblings.interop() else []
    if explicit:
        distro = wanted[len(connections.WSL) :]
    elif connections.is_sibling(saved):
        distro = saved[len(connections.WSL) :]
    else:
        distro = siblings.match(wanted or name, available)
    if not (distro or via == "wsl"):
        return False
    if distro and via is None and not explicit and name:
        # NAME may also reach an SSH host: through the SSH config, or through a saved SSH target.
        if name in connections.hosts(home):
            raise ValueError(
                f"{name} names a WSL distro and an SSH config host. Add --via wsl or --via ssh."
            )
        if saved and not connections.is_sibling(saved):
            raise ValueError(
                f"{name} names a WSL distro and a saved SSH target. Add --via wsl or --via ssh."
            )
    if not siblings.interop():
        raise ValueError(
            "wsl.exe is unavailable, so Fleet cannot reach a sibling. Next: "
            + connections.INTEROP_STEP
        )
    if not distro or distro not in available:
        raise ValueError(f"No WSL distro named {distro or wanted or name}. Run wsl.exe --list.")
    if recorded.get("enrolled") and member != registry.machine_name(home):
        raise ValueError(
            f"{name} is already a fleet member. Run {current_command(home)} update {member}."
        )
    if distro == siblings.own_distro():
        raise ValueError("This distro is this machine. Choose another distro.")
    # An explicit NAME that differs from the distro is the new machine's name.
    if not args.machine_name and name and name.lower() != distro.lower():
        args.machine_name = name
    if not args.machine_name and not manager.has_terminal():
        raise ValueError(
            "A scripted WSL deploy needs --machine-name; Fleet never names a machine silently."
        )
    if args.machine_name and not registry.NAME.fullmatch(args.machine_name):
        raise ValueError("A machine name uses letters, digits, dots, hyphens, and underscores.")
    args.name = args.machine_name or distro
    args.ssh_target = connections.WSL + distro
    if not args.return_target and siblings.own_distro():
        # The sibling reaches this distro the same way: through wsl.exe.
        args.return_target = connections.WSL + siblings.own_distro()
    return True


def set_machine_ssh(home, hostname, target):
    remote.validate_target(hostname)
    remote.validate_target(target)

    def connect(data):
        if hostname not in data["machines"]:
            # A connection can be saved before any facts have been imported from that machine.
            data["machines"][hostname] = {
                name: False if kind is bool else "" for name, kind in registry.FIELDS.items()
            }
            data["machines"][hostname].update(hostname=hostname, result="not recorded")
        data["machines"][hostname]["ssh_target"] = target

    result = change_registry(home, connect)
    return result or remote.test_connection(home, hostname, target)


def change_registry(home, edit):
    import difflib
    import fcntl

    path = home / registry.REGISTRY
    if not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError("Fleet registry parent leaves HOME.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = registry.load(home)
        edit(data)
        before = path.read_text() if path.exists() else ""
        after = registry.serialize(data)
        account = RunAccount(home, ROOT)
        account.checkouts = []
        account.software = []
        account.manifest = manager.backup_inventory(home, [registry.REGISTRY])
        account.files = (
            []
            if before == after
            else [
                {
                    "path": str(path),
                    "relative": registry.REGISTRY,
                    "action": "changed" if path.exists() else "added",
                    "before": manager.fingerprint(path),
                }
            ]
        )
        folder = manager.backup_location(home)
        account.locations["Backup folder"] = str(folder)
        account.locations["Converted source"] = read_toml(
            home / ".config/chezmoi/chezmoi.toml"
        ).get("sourceDir", "not configured")
        with tempfile.TemporaryDirectory() as temporary:
            account.diff_path = Path(temporary) / "registry.patch"
            atomic(
                account.diff_path,
                "".join(
                    difflib.unified_diff(
                        before.splitlines(True),
                        after.splitlines(True),
                        fromfile=str(path),
                        tofile=str(path),
                    )
                ),
            )
            if ui_channel.active() and not ui_channel.review(account.record()):
                return account.finish("CANCELLED")
            run_index.stage("backup")
            manager.backup(home, [registry.REGISTRY], folder)
            account.bind_backup(folder)
            atomic(path, after)
            account.confirm_files(manager.fingerprint)
    return account.finish(
        "SUCCESS",
        [],
        f"differences remain; run {current_command(home)} diff" if status(home) else "clean",
    )


def snapshot(home, operations):
    account = RunAccount(home, ROOT)
    account.checkouts = []
    account.software = []
    account.manifest = manager.backup_inventory(home, [name for name, _ in operations])
    folder = manager.backup_location(home)
    account.locations.update(
        {
            "Backup folder": str(folder),
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", "not configured"
            ),
        }
    )
    with tempfile.TemporaryDirectory() as temporary:
        account.diff_path = Path(temporary) / "diff"
        atomic(
            account.diff_path,
            run("chezmoi", "--no-pager", "--use-builtin-diff", "--color=false", "diff"),
        )
        account.status = status(home)
        run_index.stage("backup")
        manager.backup(home, [name for name, _ in operations], folder)
        account.bind_backup(folder)
        source = home / ".fleet/chezmoi-source"
        if source.exists():
            shutil.copytree(source, folder / "previous-chezmoi-source", symlinks=True)
        return account.finish(
            "SUCCESS",
            [],
            f"differences remain; run {current_command(home)} diff" if account.status else "clean",
        )


if __name__ == "__main__":
    sys.exit(style.run_cli(lambda: main(sys.argv[1:]), ROOT))
