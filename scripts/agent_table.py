"""Read and validate the agents table in the content manifest.

The agents table declares agent configuration in a neutral format. Fleet renders it for each agent.
An item is one declared rule set, MCP server, connector, plugin, hook, or skill limit.
MCP means Model Context Protocol; an MCP server offers tools that an agent can call.

Usage: import load and selected from this module.
Help: python3 scripts/agent_table.py --help.
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re

from convert import content_domains, tracked

SCHEMA = 1
SKILLS = ".agents/skills"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
EVENTS = ("session-start", "session-end", "prompt-submit", "before-tool", "after-tool", "stop")
TOOL_CLASSES = ("shell", "edit", "read", "mcp")
KINDS = ("skills", "rules", "mcp", "connectors", "plugins", "hooks")
SHARED = {"description": str, "agents": list, "domains": list}
KEYS = {
    "skills": {"domains": list},
    "rules": {**SHARED, "file": str},
    "mcp": {
        **SHARED,
        "transport": str,
        "command": str,
        "args": list,
        "env": dict,
        "pass_env": list,
        "url": str,
        "headers": dict,
        "header_env": dict,
        "bearer_token_env": str,
    },
    "connectors": {**SHARED, "url": str},
    "plugins": {**SHARED, "sources": dict},
    "hooks": {
        **SHARED,
        "event": str,
        "tools": list,
        "command": str,
        "args": list,
        "timeout": int,
    },
}
REQUIRED = {
    "skills": set(),
    "rules": {"description", "file"},
    "mcp": {"description", "transport"},
    "connectors": {"description"},
    "plugins": {"description", "sources"},
    "hooks": {"description", "event", "command"},
}
TRANSPORT_KEYS = {
    "stdio": ({"command"}, {"command", "args", "env", "pass_env"}),
    "http": ({"url"}, {"url", "headers", "header_env", "bearer_token_env"}),
}


@dataclass(frozen=True)
class Item:
    """One validated declaration. The key is KIND.NAME, such as mcp.docs-search."""

    kind: str
    name: str
    spec: dict = field(hash=False)
    # Pulled-in items read their files from ~/.fleet/marketplace-packages, not the content checkout.
    root: object = field(default=None, hash=False, compare=False)
    origin: str = "content"

    @property
    def key(self):
        return f"{self.kind}.{self.name}"


@dataclass
class Table:
    items: list
    warnings: list

    def find(self, key):
        return next((item for item in self.items if item.key == key), None)


def fail(where, problem):
    raise ValueError(f"Invalid agents table at {where}: {problem}")


def check_type(where, value, expected):
    if expected is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    else:
        valid = isinstance(value, expected)
    if not valid:
        fail(where, f"expected {expected.__name__}, found {type(value).__name__}")


def check_strings(where, values, pattern=None, label="value"):
    for value in values:
        if not isinstance(value, str) or (pattern and not pattern.fullmatch(value)):
            fail(where, f"invalid {label}: {value!r}")


def check_path(where, value, entries, executable=False):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        fail(where, f"{value} must be a relative path inside the repository")
    mode = entries.get(path.as_posix())
    if mode is None:
        fail(where, f"{value} is not a file that Git tracks")
    if executable and mode != "100755":
        fail(where, f"{value} must be executable in Git (mode 100755)")


def check_secret_free(where, mapping):
    from enrollment import SECRET

    for key, value in mapping.items():
        check_strings(where, [key, value])
        if SECRET.search(f"{key}={value}".encode()):
            fail(where, f"{key} looks like a secret; name a variable with pass_env instead")


def check_mcp(where, spec):
    transport = spec.get("transport")
    if transport not in TRANSPORT_KEYS:
        fail(where, "transport must be stdio or http")
    required, allowed = TRANSPORT_KEYS[transport]
    other = set().union(*(keys for _, keys in TRANSPORT_KEYS.values())) - allowed
    misplaced = sorted(other & set(spec))
    if misplaced:
        fail(where, f"{', '.join(misplaced)} cannot apply to the {transport} transport")
    missing = sorted(required - set(spec))
    if missing:
        fail(where, f"the {transport} transport needs {', '.join(missing)}")
    check_strings(where, spec.get("args", []))
    check_strings(where, spec.get("pass_env", []), VARIABLE, "variable name")
    check_secret_free(where, spec.get("env", {}))
    check_secret_free(where, spec.get("headers", {}))
    for header, variable in spec.get("header_env", {}).items():
        check_strings(where, [header])
        check_strings(where, [variable], VARIABLE, "variable name")
    if "bearer_token_env" in spec:
        check_strings(where, [spec["bearer_token_env"]], VARIABLE, "variable name")
    if "url" in spec and not spec["url"].startswith(("https://", "http://")):
        fail(where, "url must start with https:// or http://")


def check_hook(where, spec, entries):
    if spec["event"] not in EVENTS:
        fail(where, "event must be one of " + ", ".join(EVENTS))
    check_strings(where, spec.get("tools", []), label="tool class")
    unknown = sorted(set(spec.get("tools", [])) - set(TOOL_CLASSES))
    if unknown:
        fail(where, "tools accepts " + ", ".join(TOOL_CLASSES))
    if spec.get("tools") and spec["event"] not in ("before-tool", "after-tool"):
        fail(where, "tools applies only to before-tool and after-tool events")
    check_path(where, spec["command"], entries, executable=True)
    check_strings(where, spec.get("args", []))
    if "timeout" in spec and spec["timeout"] < 1:
        fail(where, "timeout must be a positive number of seconds")


def check_plugin(where, spec, catalog, warnings):
    if not spec["sources"]:
        fail(where, "sources needs at least one agent")
    for agent, source in spec["sources"].items():
        if not isinstance(source, dict) or set(source) - {"id", "marketplace"}:
            fail(where, f"sources.{agent} accepts id and marketplace")
        if not isinstance(source.get("id"), str) or not source["id"]:
            fail(where, f"sources.{agent} needs an id")
        if "marketplace" in source:
            check_strings(where, [source["marketplace"]])
        if agent not in catalog:
            warnings.append(f"{where}: unknown agent {agent}; this manager skips it.")


def check_item(kind, name, spec, entries, domains, catalog, warnings):
    where = f"agents.{kind}.{name}"
    if not NAME.fullmatch(name):
        fail(where, "names use lowercase letters, digits, and single hyphens")
    check_type(where, spec, dict)
    unknown = sorted(set(spec) - set(KEYS[kind]))
    if unknown:
        fail(where, "unknown key " + ", ".join(unknown))
    missing = sorted(REQUIRED[kind] - set(spec))
    if missing:
        fail(where, "missing " + ", ".join(missing))
    for key, value in spec.items():
        check_type(f"{where}.{key}", value, KEYS[kind][key])
    if "domains" in spec:
        check_strings(where, spec["domains"], label="domain")
        if "core" in spec["domains"]:
            fail(where, "core always applies. Omit domains to reach every machine.")
        undeclared = sorted(set(spec["domains"]) - set(domains))
        if undeclared:
            fail(where, "the content does not declare domain " + ", ".join(undeclared))
    if "agents" in spec:
        check_strings(where, spec["agents"], label="agent name")
        for agent in spec["agents"]:
            if agent not in catalog:
                warnings.append(f"{where}: unknown agent {agent}; this manager skips it.")
    if kind == "skills":
        folder = f"{SKILLS}/{name}/SKILL.md"
        if folder not in entries:
            fail(where, f"no tracked skill folder: {folder}")
    elif kind == "rules":
        check_path(where, spec["file"], entries)
    elif kind == "mcp":
        check_mcp(where, spec)
    elif kind == "connectors":
        if name == "fleet":
            fail(where, "fleet is the gateway's entry name; choose another connector name")
        if "url" in spec and not spec["url"].startswith("https://"):
            fail(where, "url must start with https://")
    elif kind == "plugins":
        check_plugin(where, spec, catalog, warnings)
    elif kind == "hooks":
        check_hook(where, spec, entries)


def load(checkout, manifest, catalog):
    """Validate the whole table before any plan or write. One failure stops the run."""
    table = manifest.get("agents")
    if table is None:
        return Table([], [])
    check_type("agents", table, dict)
    unknown = sorted(set(table) - {"schema", *KINDS})
    if unknown:
        fail("agents", "unknown key " + ", ".join(unknown))
    if table.get("schema") != SCHEMA:
        fail("agents.schema", f"this manager reads schema {SCHEMA}; update Fleet for a newer one")
    entries = tracked(checkout)
    domains = content_domains(manifest)
    items, warnings = [], []
    for kind in KINDS:
        section = table.get(kind, {})
        check_type(f"agents.{kind}", section, dict)
        for name, spec in section.items():
            check_item(kind, name, spec, entries, domains, catalog, warnings)
            items.append(Item(kind, name, spec))
    return Table(items, warnings)


def skills(checkout):
    """List skill folders: direct children of .agents/skills that hold SKILL.md."""
    names = set()
    for name in tracked(checkout):
        parts = PurePosixPath(name).parts
        if len(parts) == 4 and "/".join(parts[:2]) == SKILLS and parts[3] == "SKILL.md":
            names.add(parts[2])
    return sorted(names)


def exceptions(profile):
    """Return this machine's exception list: item keys mapped to off or local."""
    return dict(profile.get("agent_items", {}))


def applies(item, profile, agent=None):
    """Decide whether an item reaches this machine and, when named, one agent."""
    spec = item.spec
    if "domains" in spec and profile.get("domain", "core") not in spec["domains"]:
        return False
    if exceptions(profile).get(item.key) == "off":
        return False
    if agent is None:
        return True
    if item.kind == "plugins":
        return agent in spec["sources"] and agent in spec.get("agents", [agent])
    return agent in spec.get("agents", [agent])


def selected(table, profile, agent=None):
    return [item for item in table.items if applies(item, profile, agent)]


def usage():
    import argparse

    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from pathlib import Path

        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
