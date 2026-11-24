"""Record planned actions and confirmed outcomes for one installer run.

This internal module has no command-line interface. Use install.sh --help.
Diff contents stay in the private backup. Reports contain paths and profile choices only.

Usage: import this module from Fleet.
Help: python3 scripts/run_account.py --help.
Direct execution without help keeps its existing behavior.
"""

import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from common import atomic, read_toml, run
from command_display import command_name
from render_run import render
from installation import command_software, MANAGER
import registry
import style
import run_index
from store_migration import STATE_RECORDS, recorded_moves, restore
import dotfiles_library

RECORD = ".fleet/last-run.json"


def commit(path):
    if not (path / ".git").exists():
        return None
    return run("git", "-C", str(path), "rev-parse", "HEAD").strip()


def version(path):
    result = subprocess.run([str(path), "--version"], text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f"Cannot read software version: {path}: {result.stderr.strip()}")
    match = re.search(r"\d+\.\d+\.\d+[^\s,]*", result.stdout)
    return match.group() if match else result.stdout.splitlines()[0].strip()


def profile_changes(old, new):
    changes = []
    for field in ("domain", "domain_chosen", "git_name", "git_email", "content_repo", "format"):
        if old.get(field) != new.get(field):
            changes.append({"name": field, "old": old.get(field), "new": new.get(field)})
    for field in ("pins", "groups", "agents", "agent_items"):
        for key in sorted(set(old.get(field, {})) | set(new.get(field, {}))):
            before, after = old.get(field, {}).get(key), new.get(field, {}).get(key)
            if before != after:
                changes.append({"name": f"{field}.{key}", "old": before, "new": after})
    for tool in sorted(set(old.get("unpinned_tools", [])) ^ set(new.get("unpinned_tools", []))):
        changes.append(
            {
                "name": "defaults." + tool,
                "old": tool not in old.get("unpinned_tools", []),
                "new": tool not in new.get("unpinned_tools", []),
            }
        )
    return changes


class RunAccount:
    def __init__(self, home, root):
        self.home, self.root = home, root
        registry.load(home)  # Reject invalid state before any apply.
        self.manager_version = (root / "VERSION").read_text().strip()
        saved = home / RECORD
        previous = json.loads(saved.read_text()) if saved.exists() else {}
        self.old_profile = read_toml(home / ".fleet/profile.toml")
        self.instruction_commands = dict.fromkeys(
            ("before", "after"), command_name(self.old_profile)
        )
        self.software = []
        for name in ("chezmoi", "mise"):
            staged = os.environ.get("DOTFLEET_STAGED_" + name.upper())
            executable = staged or shutil.which(name)
            if not executable:
                continue
            destination = home / ".local/bin" / name if staged else Path(executable)
            old = version(destination) if staged and destination.exists() else None
            self.software.append(
                {
                    "name": "Mise" if name == "mise" else name,
                    "version": version(executable),
                    "path": str(destination),
                    "old": old,
                    "action": "upgraded" if old else "installed" if staged else "present",
                }
            )
        self.software.append(command_software(home, root))
        self.checkouts = [
            {
                "name": "Manager",
                "path": str(home / MANAGER),
                "old": previous.get("manager_commit"),
                "new": commit(root),
            },
            {
                "name": "Content",
                "path": str(dotfiles_library.active(home)),
                "old": commit(dotfiles_library.active(home)),
                "new": None,
            },
        ]
        self.locations = {
            "Content checkout": str(dotfiles_library.active(home)),
            "Profile file": str(home / ".fleet/profile.toml"),
            "Converted source": "None (not prepared)",
            "Backup folder": "None (not created)",
        }
        self.files = []
        self.profile = []
        self.store_moves = recorded_moves()
        self.store_restored = []
        self.applied_files = set()
        self.applied_software = set()
        self.applied_checkouts = set()
        self.profile_written = False
        self.manifest = []
        self.diff_path = None
        self.status = ""
        self.backup = None
        self.result = None
        self.warnings = []
        self.status_after = None
        self.selected_profile = {}
        self.available_defaults = {}
        self.adoption_excluded = []
        self.scan = {}
        self.debug_details = None
        self.agent_steps = []
        # A first enrollment ends with a check and a short account of its stages.
        self.enrollment = None
        self.update_triggers = json.loads(os.environ.get("FLEET_UPDATE_TRIGGERS", "[]"))
        if not isinstance(self.update_triggers, list) or any(
            trigger not in ("manager update", "content update", "drift repair", "profile change")
            for trigger in self.update_triggers
        ):
            raise ValueError("Invalid update reasons. Run update again.")
        style.ACTIVE_RECORD = self

    def plan(self, plan, checkout, profile, backup_path, manifest):
        self.checkouts[1]["new"] = commit(checkout)
        self.locations["Converted source"] = str(plan["config"]["sourceDir"])
        self.locations["Backup folder"] = str(backup_path)
        self.profile = profile_changes(self.old_profile, profile)
        self.instruction_commands["after"] = command_name(profile)
        self.selected_profile = {
            key: profile.get(key)
            for key in [
                "content_repo",
                "domain",
                "git_name",
                "git_email",
                "pins",
                "groups",
                "agents",
            ]
        }
        self.available_defaults = plan["available"]
        self.adoption_excluded = profile.get("adoption_excluded", [])
        self.manifest = copy.deepcopy(manifest)
        before = {item["path"]: item for item in manifest}
        self.files = []
        for name in sorted(plan["changed"]):
            if (
                name == registry.REGISTRY
                or name in STATE_RECORDS
                or any(
                    str(self.home / name) == item["path"] and item["action"] != "present"
                    for item in self.software
                )
            ):
                continue  # Bookkeeping and software have their own records, not home-setting diffs.
            entry = before[name]
            agent_stale = plan.get("agents").stale_paths if plan.get("agents") else {}
            action = (
                "removed"
                if name in plan["stale"] or name in plan["removed"] or name in agent_stale
                else "added"
                if entry["state"] == "absent"
                else "changed"
            )
            self.files.append(
                {
                    "path": str(self.home / name),
                    "action": action,
                    "before": entry.get("fingerprint"),
                    "relative": name,
                }
            )
        self.diff_path = plan["diff_path"]
        self.status = plan["status"]
        agents = plan.get("agents")
        self.agent_steps = (agents.kept + agents.notes + agents.steps) if agents else []
        if agents:
            import agent_deploy

            # Plugin actions are software changes. Replan replaces earlier rows.
            self.software = [item for item in self.software if not item.get("plugin")]
            self.software += agent_deploy.plugin_software(agents)
        run_index.stage("review")

    def bind_backup(self, path):
        self.backup = path
        run_index.attach(self.record())
        recorded = json.loads((path / "MANIFEST.json").read_text())
        if recorded != self.manifest:
            raise ValueError(
                "Backup records differ from the reviewed plan. Nothing applied; run setup again."
            )
        atomic(path / "CHEZMOI-DIFF.patch", self.diff_path.read_bytes())
        self.save()
        run_index.stage("apply")

    def record(self):
        return {
            "home": str(self.home),
            "software": self.software,
            "checkouts": self.checkouts,
            "files": self.files,
            "profile_changes": self.profile,
            "store_moves": self.store_moves,
            "store_restored": self.store_restored,
            "locations": self.locations,
            "backup_manifest": self.manifest,
            "chezmoi_status_before": self.status,
            "completed_files": sorted(self.applied_files),
            "completed_software": sorted(self.applied_software),
            "completed_checkouts": sorted(self.applied_checkouts),
            "profile_written": self.profile_written,
            "backup_created": bool(self.backup),
            "result": self.result,
            "warnings": self.warnings,
            "chezmoi_status_after": self.status_after,
            "selected_profile": self.selected_profile,
            "instruction_commands": self.instruction_commands,
            "available_defaults": self.available_defaults,
            "adoption_excluded": self.adoption_excluded,
            "scan": self.scan,
            "debug_details": self.debug_details,
            "update_triggers": self.update_triggers,
            "agent_steps": self.agent_steps,
            "enrollment": self.enrollment,
        }

    def save(self):
        if self.backup:
            atomic(self.backup / "RUN.json", json.dumps(self.record(), indent=2) + "\n")

    def confirm_files(self, fingerprint):
        # Verify recorded operations, including partial apply. Never infer new paths or categories here.
        for item in self.files:
            current = fingerprint(Path(item["path"]))
            if current != item["before"] and (item["action"] != "removed" or current is None):
                self.applied_files.add(item["path"])
        self.save()

    def lines(self, future=False):
        return render(self.record(), future=future).rstrip().splitlines()

    def finish(self, result, warnings=None, status=None):
        self.result = result
        self.warnings = warnings or []
        self.status_after = status
        if result == "CANCELLED" and self.store_moves:
            # A cancelled run leaves the machine as it was, so this run's store moves are undone.
            try:
                self.store_restored = restore(self.home)
                self.store_moves = []
            except (OSError, ValueError) as error:
                self.warnings.append("Stores were not moved back: " + str(error))
        if self.backup:
            try:
                registry.refresh(
                    self.home, self.manager_version, self.backup, result, self.old_profile
                )
            except (OSError, ValueError, RuntimeError) as error:
                self.result = "FAILED"
                self.warnings.append("Fleet registry was not refreshed: " + str(error))
        self.save()
        run_index.attach(self.record())
        run_index.outcome(
            self.result, self.warnings[-1] if self.result == "FAILED" and self.warnings else ""
        )
        print(render(self.record()), end="")
        marker = os.environ.get("DOTFLEET_SUMMARY_MARKER")
        if marker:
            Path(marker).touch()
        return 1 if self.result == "FAILED" else 0


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
