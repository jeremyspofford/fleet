"""Render pending file patches with isolated chezmoi sources and state.

Usage: import this module from Fleet.
Help: python3 scripts/preview_diff.py --help.
Direct execution without help keeps its existing behavior.
"""

from common import atomic, run, toml
from convert import USER_MISE, source_name
from installation import COMMAND, launcher
from store_migration import STATE_RECORDS


def patches(home, profile, plan, folder, root):
    """Return real chezmoi patches, including files written directly by the manager."""
    extra = folder / "extra"
    extra.mkdir()
    payloads = {
        ".config/chezmoi/chezmoi.toml": toml(plan["config"]),
        ".fleet/profile.toml": toml(profile),
        COMMAND: launcher(root),
    }
    if plan["native"]:
        payloads.update(plan["generated"])
    agents = plan["agents"].payloads(home)
    extra_names = set()
    for name in sorted(plan["changed"] | plan["removed"]):
        if name in STATE_RECORDS:
            continue  # Inventories describe writes; they are not rendered settings.
        target = extra / source_name(name, "100755" if name == COMMAND else "100644")
        handover = plan.get("mise_handover") if name == USER_MISE else None
        if handover is not None:
            # The old Mise config keeps only the user's entries, or goes when none remain.
            # The new preview source no longer manages it, so the extra source renders it.
            if handover:
                atomic(target, handover)
            else:
                target = target.with_name("remove_" + target.name)
                atomic(target, "")
        elif name in plan["stale"] or (name in agents and agents[name] is None):
            target = target.with_name("remove_" + target.name)
            atomic(target, "")
        elif isinstance(agents.get(name), tuple):
            target = extra / source_name(name, "120000")
            atomic(target, agents[name][1])
        elif name in agents:
            atomic(target, agents[name])
        elif name in payloads:
            atomic(target, payloads[name])
        else:
            continue
        extra_names.add(name)
    extra_config = folder / "extra.toml"
    atomic(extra_config, toml({"sourceDir": str(extra), "destDir": str(home), "umask": 0o077}))
    result = []
    for name in sorted((plan["changed"] | plan["removed"]) - {".fleet/agents.json"}):
        config = extra_config if name in extra_names else folder / "preview.toml"
        patch = run(
            "chezmoi",
            "--config",
            str(config),
            "--persistent-state",
            str(folder / "diff-state.db"),
            "--cache",
            str(folder / "diff-cache"),
            "--no-pager",
            "--use-builtin-diff",
            "--color=false",
            "diff",
            str(home / name),
        )
        if patch:
            result.append((name, patch))
    return result


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
