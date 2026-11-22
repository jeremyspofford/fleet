"""Plan and apply the agent deployment tree, its pointers, and owned entries.

Fleet deploys one payload per machine: the agent configuration in a Fleet-owned tree.
Each agent gets one pointer to the tree. A pointer is a link, or one entry that names a tree file.
An owned entry is one named value that Fleet writes inside a file that an agent also writes.
Agents that accept no pointer for a part receive owned entries for that part.
The inventory lists every output and entry, so Fleet can rebuild, compare, or remove each one.

Usage: python3 scripts/agent_deploy.py [--home PATH]
Parameters: --home selects a full target home path; the default is the current user's home.
The command prints the planned outputs for the saved profile and content checkout. It writes nothing.
"""

# Exit codes: 0 success or help; 1 invalid content or conflict; 2 invalid arguments.

import argparse
import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

import agent_table
from common import atomic, value
import dotfiles_library

ROOT = Path(__file__).resolve().parents[1]
TREE = ".fleet/agents"
INVENTORY = ".fleet/agents.json"
SCHEMA = 2
SKILLS = ".agents/skills"
PLUGIN_DESCRIPTION = "Agent configuration rendered by Fleet."

# Event names come from each agent's documentation; Gemini CLI's come from its migrate command.
EVENTS = {
    "claude": {
        "session-start": "SessionStart",
        "session-end": "SessionEnd",
        "prompt-submit": "UserPromptSubmit",
        "before-tool": "PreToolUse",
        "after-tool": "PostToolUse",
        "stop": "Stop",
    },
    "gemini": {
        "session-start": "SessionStart",
        "session-end": "SessionEnd",
        "prompt-submit": "BeforeAgent",
        "before-tool": "BeforeTool",
        "after-tool": "AfterTool",
        "stop": "AfterAgent",
    },
}
EVENTS["codex"] = EVENTS["claude"]
# Tool classes map to matchers. A class without a confirmed matcher skips the hook for that agent.
MATCHERS = {
    "claude": {"shell": "Bash", "edit": "Edit|MultiEdit|Write", "read": "Read", "mcp": "mcp__.*"},
    "codex": {"shell": "Bash"},
    "gemini": {"shell": "run_shell_command"},
}
TIMEOUT_SCALE = {"gemini": 1000}  # Gemini CLI counts milliseconds; the table counts seconds.
REFERENCE = {"claude": "${%s}", "gemini": "${%s}", "pi": "${%s}", "opencode": "{env:%s}"}
SIGN_IN = {
    "claude": "run /mcp in Claude Code and choose {name}",
    "codex": "run codex mcp login {name}",
    "gemini": "run /mcp auth {name} in Gemini CLI",
    "opencode": "run opencode mcp auth {name}",
    "pi": "run pi mcp login {name}",
}
OPENCODE_SCHEMA = "https://opencode.ai/config.json"
PI_RULES = ".pi/agent/APPEND_SYSTEM.md"
GATEWAY = "fleet"  # The one server entry that each agent receives for every declared MCP server.
MANAGER = ".fleet/manager"


# These agents install a listed plugin by themselves at start (tested 2026-11-18).
# Fleet writes their listing; Gemini CLI and Codex still use their installers.
LISTED = ("claude", "opencode", "pi")


def marketplace_source(marketplace):
    """Describe a marketplace address in Claude Code's extraKnownMarketplaces form."""
    if marketplace.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return {"source": "git", "url": marketplace}
    if marketplace.startswith("/"):
        return {"source": "directory", "path": marketplace}
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", marketplace):
        return {"source": "github", "repo": marketplace}
    return {"source": "git", "url": marketplace}


def extension_name(source):
    """Gemini CLI uninstalls by extension name: the last part of its source address."""
    return source.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


# Plugins are fully managed through each agent's own installer, driven by the inventory.
# Fleet's review is the consent step. Claude Code still asks before any marketplace command.
PLUGIN_COMMANDS = {
    "claude": {
        "marketplace": lambda command, source: [command, "plugin", "marketplace", "add", source],
        "install": lambda command, source: [
            command,
            "plugin",
            "install",
            source,
            "--scope",
            "user",
        ],
        "uninstall": lambda command, source: [
            command,
            "plugin",
            "uninstall",
            source,
            "--scope",
            "user",
        ],
    },
    "codex": {
        "marketplace": lambda command, source: [command, "plugin", "marketplace", "add", source],
        "install": lambda command, source: [command, "plugin", "add", source],
        "uninstall": lambda command, source: [command, "plugin", "remove", source],
    },
    "gemini": {
        "install": lambda command, source: [command, "extensions", "install", source, "--consent"],
        "uninstall": lambda command, source: [
            command,
            "extensions",
            "uninstall",
            extension_name(source),
        ],
    },
    "opencode": {
        "install": lambda command, source: [command, "plugin", source, "--global"],
    },
    "pi": {
        "install": lambda command, source: [command, "install", source],
        "uninstall": lambda command, source: [command, "remove", source],
    },
}


@dataclass
class Output:
    """An owned path: a tree file or a pointer link that Fleet creates and owns whole."""

    relative: str
    agent: str
    item: str
    data: bytes = None
    mode: int = 0o600
    link: str = None

    def fingerprint(self):
        if self.link is not None:
            return {"link": self.link}
        return {"sha256": hashlib.sha256(self.data).hexdigest(), "mode": self.mode}


@dataclass
class Entry:
    """An owned entry inside a shared file. Fleet owns only the keys that it wrote."""

    agent: str
    file: str
    format: str
    container: tuple
    name: str
    value: object
    item: str
    match: str = "name"  # name: a keyed table; self: a list value; command: a hook group.

    def key(self):
        return (self.file, self.container, self.name)

    def record(self):
        return {
            "agent": self.agent,
            "file": self.file,
            "format": self.format,
            "container": list(self.container),
            "name": self.name,
            "value": self.value,
            "item": self.item,
            "match": self.match,
        }


def entry_from(record):
    return Entry(
        record["agent"],
        record["file"],
        record["format"],
        tuple(record["container"]),
        record["name"],
        record["value"],
        record["item"],
        record.get("match", "name"),
    )


@dataclass
class Plan:
    outputs: dict = field(default_factory=dict)
    entries: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stale_paths: dict = field(default_factory=dict)
    stale_entries: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    released: list = field(default_factory=list)
    released_entries: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    adopted: list = field(default_factory=list)
    previous: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    plugins: list = field(default_factory=list)
    plugin_installs: list = field(default_factory=list)
    plugin_removals: list = field(default_factory=list)
    released_plugins: list = field(default_factory=list)
    reported: list = field(default_factory=list)
    plugin_steps: dict = field(default_factory=dict)

    def changed_paths(self, home):
        """Home-relative paths that apply would create, replace, or remove."""
        result = set()
        for relative, output in self.outputs.items():
            if fingerprint(home / relative) != output.fingerprint():
                result.add(relative)
        result |= set(self.stale_paths)
        result |= {name for name, (before, after, _) in self.files.items() if before != after}
        return result

    def payloads(self, home):
        """Return planned contents for diff previews: bytes, a link target, or None to remove."""
        result = {}
        for relative, output in self.outputs.items():
            result[relative] = ("link", output.link) if output.link is not None else output.data
        for relative, (_, _, text) in self.files.items():
            result[relative] = text.encode()
        for relative in self.stale_paths:
            result[relative] = None
        return result

    def all_paths(self):
        return set(self.outputs) | set(self.stale_paths) | set(self.files)


def fingerprint(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if path.is_file():
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": path.stat().st_mode & 0o777,
        }
    return None


def load_inventory(home):
    """Read the inventory. Convert the earlier flat record into stale owned paths."""
    path = home / INVENTORY
    legacy = home / ".local/state/dotfiles/agents.json"
    source = path if path.exists() else legacy
    if path.is_symlink():
        raise ValueError("Refusing a symlinked agent inventory")
    data = json.loads(source.read_text()) if source.exists() else {}
    if data.get("schema_version") == SCHEMA:
        return data
    paths = {}
    for relative, record in data.items():
        saved = {"link": record["link"]} if "link" in record else {"sha256": record["sha256"]}
        paths[relative] = {"agent": record.get("agent", ""), "item": "legacy", "fingerprint": saved}
    return {"schema_version": SCHEMA, "paths": paths, "entries": [], "plugins": []}


def matches(path, saved):
    """Compare a path with its record. Legacy file records hold a hash without a mode."""
    current = fingerprint(path)
    if current is None:
        return False
    return all(current.get(key) == item for key, item in saved.items())


def blob(checkout, name):
    return subprocess.check_output(["git", "-C", str(checkout), "show", f":{name}"])


def reference(agent, name):
    return REFERENCE[agent] % name


def server(agent, spec):
    """Render one MCP server or connector in an agent's own shape."""
    transport = spec.get("transport", "http")
    env = dict(spec.get("env", {}))
    headers = dict(spec.get("headers", {}))
    if agent == "codex":
        if transport == "stdio":
            result = {"command": spec["command"], "args": spec.get("args", [])}
            if env:
                result["env"] = env
            if spec.get("pass_env"):
                result["env_vars"] = spec["pass_env"]
            return result
        result = {"url": spec["url"]}
        if headers:
            result["http_headers"] = headers
        if spec.get("header_env"):
            result["env_http_headers"] = spec["header_env"]
        if spec.get("bearer_token_env"):
            result["bearer_token_env_var"] = spec["bearer_token_env"]
        return result
    if agent == "antigravity":
        # agy passes ${NAME} through literally; its servers inherit agy's environment instead.
        # agy also drops an empty args list when it rewrites the file (tested 2026-11-18).
        result = {"command": spec["command"]}
        if spec.get("args"):
            result["args"] = spec["args"]
        if env:
            result["env"] = env
        return result
    env.update({name: reference(agent, name) for name in spec.get("pass_env", [])})
    headers.update(
        {key: reference(agent, name) for key, name in spec.get("header_env", {}).items()}
    )
    if spec.get("bearer_token_env"):
        headers["Authorization"] = "Bearer " + reference(agent, spec["bearer_token_env"])
    if agent == "opencode":
        if transport == "stdio":
            result = {"type": "local", "command": [spec["command"], *spec.get("args", [])]}
            if env:
                result["environment"] = env
            return result
        result = {"type": "remote", "url": spec["url"]}
    elif transport == "stdio":
        result = {"command": spec["command"], "args": spec.get("args", [])}
        if env:
            result["env"] = env
        return result
    elif agent == "claude":
        result = {"type": "http", "url": spec["url"]}
    elif agent == "gemini":
        result = {"httpUrl": spec["url"]}
    else:
        result = {"url": spec["url"]}
    if headers:
        result["headers"] = headers
    return result


def hook_group(agent, spec, command):
    """Render one hook as an event group. Return None when a tool class has no matcher."""
    group = {}
    if spec.get("tools"):
        known = MATCHERS[agent]
        if any(tool not in known for tool in spec["tools"]):
            return None
        group["matcher"] = "|".join(known[tool] for tool in spec["tools"])
    hook = {"type": "command", "command": command}
    if "timeout" in spec:
        hook["timeout"] = spec["timeout"] * TIMEOUT_SCALE.get(agent, 1)
    group["hooks"] = [hook]
    return group


def json_bytes(data):
    return (json.dumps(data, indent=2) + "\n").encode()


class Planner:
    def __init__(self, home, checkout, profile, table, catalog):
        self.home, self.checkout, self.profile = home, checkout, profile
        self.table, self.catalog = table, catalog
        self.tree = home / TREE
        self.plan = Plan()
        self.exceptions = agent_table.exceptions(profile)

    def add(self, relative, agent, item, data=None, mode=0o600, link=None):
        if relative in self.plan.outputs and self.plan.outputs[relative].agent != agent:
            raise ValueError(f"Two agents own one output: {relative}")
        self.plan.outputs[relative] = Output(relative, agent, item, data, mode, link)

    def entry(self, agent, file, fmt, container, name, rendered, item, match="name"):
        self.plan.entries.append(Entry(agent, file, fmt, container, name, rendered, item, match))

    def items(self, agent, *kinds):
        chosen = []
        for item in agent_table.selected(self.table, self.profile, agent):
            if item.kind not in kinds:
                continue
            if self.exceptions.get(item.key) == "local":
                self.plan.notes.append(f"{agent}: {item.key} keeps this machine's own value.")
                continue
            chosen.append(item)
        return chosen

    def gateway(self, agent, items):
        """Serve every selected MCP server through one gateway entry for this agent.

        The gateway file lists downstream servers. A tree wrapper starts the gateway with it.
        The entry names no variable: the gateway reads ~/.secrets itself, so a new server
        changes no agent settings file.
        """
        downstream = {}
        for item in items:
            spec = {
                key: value
                for key, value in item.spec.items()
                if key not in ("description", "agents", "domains")
            }
            downstream[item.name] = spec
        config = f"{TREE}/gateway/{agent}.json"
        self.add(config, agent, "gateway", json_bytes({"servers": downstream}))
        wrapper = f"{TREE}/gateway/run-{agent}"
        script = self.home / MANAGER / "scripts/gateway.py"
        text = (
            "#!/bin/sh\n# Generated by Fleet. The gateway serves every declared MCP server.\n"
            # The agent's own PATH finds Python at start. The wrapper bytes never change by caller.
            f"exec /usr/bin/env python3 {shlex.quote(str(script))}"
            f' --config {shlex.quote(str(self.home / config))} "$@"\n'
        )
        self.add(wrapper, agent, "gateway", text.encode(), 0o700)
        entry = {"transport": "stdio", "command": str(self.home / wrapper), "pass_env": []}
        return agent_table.Item("gateway", GATEWAY, {}), server(agent, entry)

    def servers(self, agent):
        """Return the gateway entry and connectors, with sign-in steps and skips."""
        result = {}
        served = self.items(agent, "mcp")
        if served:
            result[GATEWAY] = self.gateway(agent, served)
        for item in self.items(agent, "connectors"):
            spec = item.spec
            if "url" not in spec:
                self.plan.steps.append(
                    f"{agent}: connect {item.name} in the agent; the content gives no address."
                )
                continue
            result[item.name] = (item, server(agent, spec))
            self.plan.steps.append(
                f"{agent}: sign in to {item.name}: " + SIGN_IN[agent].format(name=item.name)
            )
        return result

    def rule_text(self, agent):
        """Return the agent's declared rule sets as one text, or None when it has none."""
        chosen = self.items(agent, "rules")
        if not chosen:
            return None
        parts = []
        for item in chosen:
            text = blob(item.root or self.checkout, item.spec["file"]).decode()
            parts.append(f"<!-- Fleet rule set: {item.name} -->\n" + text.rstrip("\n") + "\n")
        return "\n".join(parts)

    def rules(self, agent):
        text = self.rule_text(agent)
        if text is None:
            return None
        relative = f"{TREE}/rules/{agent}.md"
        self.add(relative, agent, "rules", text.encode())
        return relative

    def hooks(self, agent):
        """Copy hook scripts into the tree and return grouped hook events."""
        events = {}
        for item in self.items(agent, "hooks"):
            spec = item.spec
            if agent not in EVENTS:
                self.plan.notes.append(f"{agent}: hook {item.name} is unsupported for this agent.")
                continue
            script = f"{TREE}/hooks/{item.name}/{Path(spec['command']).name}"
            source = blob(item.root or self.checkout, spec["command"])
            self.add(script, "", item.key, source, 0o700)
            wrapper = f"{TREE}/hooks/{item.name}/run-{agent}"
            environment = {
                "FLEET_AGENT": agent,
                "FLEET_HOOK": item.name,
                "FLEET_HOOK_EVENT": spec["event"],
            }
            words = [str(self.home / script), *spec.get("args", [])]
            text = (
                "#!/bin/sh\n# Generated by Fleet. Edit the content repository, then update.\n"
                + "".join(
                    f"export {key}={shlex.quote(item)}\n" for key, item in environment.items()
                )
                + "exec "
                + " ".join(shlex.quote(word) for word in words)
                + ' "$@"\n'
            )
            self.add(wrapper, agent, item.key, text.encode(), 0o700)
            group = hook_group(agent, spec, str(self.home / wrapper))
            if group is None:
                self.plan.notes.append(
                    f"{agent}: hook {item.name} names a tool class without a confirmed matcher; skipped."
                )
                continue
            events.setdefault(EVENTS[agent][spec["event"]], []).append((item, group))
        return events

    def shadowed(self, agent, names):
        """Skip OpenCode servers that a later settings file overrides by name."""
        shadows = set()
        for relative in self.catalog[agent].get("shadow_paths", []):
            path = self.home / relative
            if not path.is_file():
                continue
            try:
                data = json.loads(strip_jsonc(path.read_text()))
            except ValueError:
                self.plan.notes.append(f"{agent}: cannot read ~/{relative}; shadow check skipped.")
                continue
            for name in names:
                if name in data.get("mcp", {}):
                    shadows.add(name)
                    self.plan.notes.append(
                        f"{agent}: ~/{relative} defines mcp.{name}, which overrides Fleet; skipped."
                    )
        return shadows

    def claude(self):
        base = f"{TREE}/claude"
        manifest = {"name": "fleet", "description": PLUGIN_DESCRIPTION, "version": "1.0.0"}
        self.add(f"{base}/.claude-plugin/plugin.json", "claude", "pointer", json_bytes(manifest))
        servers = {name: rendered for name, (_, rendered) in self.servers("claude").items()}
        if servers:
            self.add(f"{base}/.mcp.json", "claude", "mcp", json_bytes({"mcpServers": servers}))
        events = self.hooks("claude")
        if events:
            hooks = {event: [group for _, group in groups] for event, groups in events.items()}
            self.add(f"{base}/hooks/hooks.json", "claude", "hooks", json_bytes({"hooks": hooks}))
        # One link per skill keeps each skill's own name, /NAME, as in every other agent.
        # Claude Code 2.1.259 lists a linked ~/.claude/skills/NAME as NAME (tested 2026-11-22).
        for name in self.skill_names():
            self.add(
                f".claude/skills/{name}",
                "claude",
                f"skills.{name}",
                link=str(self.home / SKILLS / name),
            )
        self.add(".claude/skills/fleet", "claude", "pointer", link=str(self.home / base))
        rules = self.rules("claude")
        if rules:
            self.add(".claude/rules/fleet.md", "claude", "rules", link=str(self.home / rules))

    def skill_names(self):
        """Name each skill that reaches this machine: content folders and pulled-in skills."""
        declared = {
            item.name: item
            for item in self.table.items
            if item.kind == "skills" and item.origin == "content"
        }
        names = [
            name
            for name in agent_table.skills(self.checkout)
            if agent_table.applies(
                declared.get(name, agent_table.Item("skills", name, {})), self.profile
            )
        ]
        return sorted(set(names) | set(self.pulled_names))

    def gemini(self):
        base = f"{TREE}/gemini"
        manifest = {"name": "fleet", "version": "1.0.0", "description": PLUGIN_DESCRIPTION}
        rules = self.rules("gemini")
        if rules:
            manifest["contextFileName"] = "GEMINI.md"
            self.add(f"{base}/GEMINI.md", "gemini", "rules", link=str(self.home / rules))
        servers = {name: rendered for name, (_, rendered) in self.servers("gemini").items()}
        if servers:
            manifest["mcpServers"] = servers
        self.add(f"{base}/gemini-extension.json", "gemini", "pointer", json_bytes(manifest))
        events = self.hooks("gemini")
        if events:
            hooks = {event: [group for _, group in groups] for event, groups in events.items()}
            self.add(f"{base}/hooks/hooks.json", "gemini", "hooks", json_bytes({"hooks": hooks}))
        self.add(".gemini/extensions/fleet", "gemini", "pointer", link=str(self.home / base))

    def codex(self):
        spec = self.catalog["codex"]
        for name, (item, rendered) in self.servers("codex").items():
            self.entry(
                "codex", spec["mcp_file"], "toml", ("mcp_servers",), name, rendered, item.key
            )
        events = self.hooks("codex")
        for event, groups in events.items():
            for item, group in groups:
                command = group["hooks"][0]["command"]
                self.entry(
                    "codex",
                    spec["hooks_file"],
                    "json",
                    ("hooks", event),
                    command,
                    group,
                    item.key,
                    "command",
                )
        if events:
            self.plan.steps.append("codex: trust new or changed Fleet hooks in /hooks.")
        rules = self.rule_text("codex")
        if rules is not None:
            # Codex reads no include file. Its one instructions setting carries the rule text.
            self.entry(
                "codex", spec["mcp_file"], "toml", (), "developer_instructions", rules, "rules"
            )

    def opencode(self):
        spec = self.catalog["opencode"]
        servers = self.servers("opencode")
        shadows = self.shadowed("opencode", servers)
        rules = self.rules("opencode")
        if servers or rules:
            self.entry(
                "opencode", spec["mcp_file"], "json", (), "$schema", OPENCODE_SCHEMA, "pointer"
            )
        for name, (item, rendered) in servers.items():
            if name not in shadows:
                self.entry("opencode", spec["mcp_file"], "json", ("mcp",), name, rendered, item.key)
        if rules:
            path = str(self.home / rules)
            self.entry(
                "opencode", spec["mcp_file"], "json", ("instructions",), path, path, "rules", "self"
            )
        for item in self.items("opencode", "hooks"):
            self.plan.notes.append(f"opencode: hook {item.name} is unsupported for this agent.")

    def pi(self):
        spec = self.catalog["pi"]
        for name, (item, rendered) in self.servers("pi").items():
            self.entry("pi", spec["mcp_file"], "json", ("mcpServers",), name, rendered, item.key)
        for item in self.items("pi", "hooks"):
            self.plan.notes.append(f"pi: hook {item.name} is unsupported for this agent.")
        rules = self.rules("pi")
        if rules:
            # Pi 1.3.0 reads a linked APPEND_SYSTEM.md into its system prompt (tested 2026-11-20).
            self.add(PI_RULES, "pi", "rules", link=str(self.home / rules))

    def antigravity(self):
        """Write the one tested surface: the gateway entry. Report every untested item."""
        spec = self.catalog["antigravity"]
        served = self.items("antigravity", "mcp")
        if served:
            item, rendered = self.gateway("antigravity", served)
            self.entry(
                "antigravity",
                spec["mcp_file"],
                "json",
                ("mcpServers",),
                GATEWAY,
                rendered,
                item.key,
            )
        for kind, label in (("connectors", "connector"), ("rules", "rule set"), ("hooks", "hook")):
            for item in self.items("antigravity", kind):
                self.plan.notes.append(
                    f"antigravity: {label} {item.name} is untested for this agent; Fleet writes nothing."
                )

    def plugins(self, agent):
        for item in self.items(agent, "plugins"):
            source = item.spec["sources"][agent]
            if agent not in LISTED and agent not in PLUGIN_COMMANDS:
                self.plan.notes.append(
                    f"{agent}: plugin {item.name} is untested for this agent; Fleet installs nothing."
                )
                continue
            if agent in LISTED:
                self.plugin_listing(agent, item, source)
                continue
            self.plan.plugins.append(
                {
                    "agent": agent,
                    "item": item.key,
                    "id": source["id"],
                    "marketplace": source.get("marketplace", ""),
                }
            )

    def plugin_listing(self, agent, item, source):
        """Write the agent's own plugin listing. The agent installs the plugin at its next start."""
        relative = self.catalog[agent]["plugin_listing_file"]
        identifier = source["id"]
        if agent == "claude":
            self.entry(agent, relative, "json", ("enabledPlugins",), identifier, True, item.key)
            marketplace = source.get("marketplace")
            if marketplace and "@" in identifier:
                name = identifier.split("@", 1)[1]
                listing = {"source": marketplace_source(marketplace)}
                self.entry(
                    agent, relative, "json", ("extraKnownMarketplaces",), name, listing, item.key
                )
            self.plan.plugin_steps[(agent, item.key)] = (
                f"claude: start Claude Code once in a terminal; it installs {identifier}"
                " in the background and loads it from the next session."
            )
            return
        container = ("plugin",) if agent == "opencode" else ("packages",)
        self.entry(agent, relative, "json", container, identifier, identifier, item.key, "self")
        label = "OpenCode" if agent == "opencode" else "Pi"
        self.plan.plugin_steps[(agent, item.key)] = (
            f"{agent}: the next {label} start installs {identifier}."
        )

    def marketplace_skills(self):
        """Copy pulled-in skills into the tree and link each one into ~/.agents/skills."""
        for item in self.table.items:
            if item.kind != "skills" or item.origin == "content":
                continue
            if not agent_table.applies(item, self.profile):
                continue
            folder = item.spec["path"].rstrip("/")
            for name, mode in sorted(agent_table.tracked(item.root).items()):
                if name.startswith(folder + "/"):
                    relative = f"{TREE}/skills/{item.name}/{name[len(folder) + 1 :]}"
                    data = blob(item.root, name)
                    self.add(relative, "", item.key, data, 0o700 if mode == "100755" else 0o600)
            target = str(self.home / TREE / "skills" / item.name)
            self.add(f"{SKILLS}/{item.name}", "", item.key, link=target)
            self.pulled_skills = True
            self.pulled_names.append(item.name)

    def build(self, agents):
        self.pulled_skills = False
        self.pulled_names = []
        if agents:
            self.marketplace_skills()
        for agent in agents:
            getattr(self, agent)()
            self.plugins(agent)
        return self.plan


def strip_jsonc(text):
    """Remove comments and trailing commas from JSONC text, keeping strings intact."""
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
    text = pattern.sub(lambda match: match.group() if match.group()[0] == '"' else "", text)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def rendered_agents(home, profile, catalog):
    import agent_detection

    detected = agent_detection.detected(home, catalog)
    return [
        name
        for name, spec in catalog.items()
        if profile.get("agents", {}).get(name)
        and name in detected
        and spec["supported"]
        and spec.get("render", False)
    ]


def owned(container_value, entry):
    """Find Fleet's element in a keyed table or list. Return (found, element)."""
    if entry.match == "name":
        if isinstance(container_value, dict) and entry.name in container_value:
            return True, container_value[entry.name]
        return False, None
    for element in container_value if isinstance(container_value, list) else []:
        if entry.match == "self" and element == entry.name:
            return True, element
        if entry.match == "command" and isinstance(element, dict):
            hooks = element.get("hooks") or [{}]
            if isinstance(hooks[0], dict) and hooks[0].get("command") == entry.name:
                return True, element
    return False, None


def same(current, written):
    """Compare only the keys that Fleet wrote. Agent-added keys never count as drift."""
    if isinstance(written, dict):
        return isinstance(current, dict) and all(
            key in current and same(current[key], item) for key, item in written.items()
        )
    return current == written


def lookup(data, container):
    for key in container:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
        if data is None:
            return None
    return data


def read_shared(home, relative, fmt):
    """Parse a shared file. Stop when it is unsafe or unreadable."""
    path = home / relative
    if path.is_symlink() and not path.resolve().is_relative_to(home.resolve()):
        raise ValueError(f"~/{relative} is a link that leaves home; Fleet will not edit it.")
    if not path.exists():
        return None, {}
    if not path.is_file():
        raise ValueError(f"~/{relative} is not a regular file.")
    text = path.read_text()
    try:
        # Antigravity creates an empty mcp_config.json at first start. Empty means no values.
        data = (json.loads(text) if fmt == "json" else tomllib.loads(text)) if text.strip() else {}
    except ValueError as error:
        raise ValueError(f"~/{relative} is not valid {fmt.upper()}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"~/{relative} must hold a {fmt.upper()} object.")
    return text, data


def merge(current, written, previous):
    """Replace Fleet's previous keys with the new value. Keep keys that others added."""
    if not isinstance(written, dict) or not isinstance(current, dict):
        return copy.deepcopy(written)
    result = {
        key: item
        for key, item in current.items()
        if key not in (previous if isinstance(previous, dict) else {})
    }
    for key, item in written.items():
        earlier = previous.get(key) if isinstance(previous, dict) else None
        result[key] = merge(current.get(key), item, earlier)
    return result


def set_element(data, entry, element):
    """Place or remove Fleet's element. None removes it."""
    parent = data
    for key in entry.container:
        parent = parent.setdefault(
            key, {} if entry.match == "name" or key != entry.container[-1] else []
        )
    if entry.match == "name":
        if element is None:
            parent.pop(entry.name, None)
        else:
            parent[entry.name] = element
        return
    found, current = owned(parent, entry)
    if found:
        index = parent.index(current)
        if element is None:
            del parent[index]
        else:
            parent[index] = element
    elif element is not None:
        parent.append(element)


def prune(data, container):
    """Remove containers that Fleet's removal left empty."""
    for depth in range(len(container), 0, -1):
        parent = lookup(data, container[: depth - 1]) if depth > 1 else data
        key = container[depth - 1]
        if isinstance(parent, dict) and parent.get(key) in ({}, []):
            del parent[key]


TABLE_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def toml_block(text, container, name):
    """Find the header lines that define one named table and its subtables."""
    prefix = ".".join(container + (name,))
    lines = text.splitlines(keepends=True)
    owned_lines, inside = [], False
    for index, line in enumerate(lines):
        match = TABLE_HEADER.match(line)
        if match:
            key = match.group(1).replace('"', "").replace(" ", "")
            inside = key == prefix or key.startswith(prefix + ".")
            if inside and line.lstrip().startswith("[["):
                raise ValueError(f"An array table defines {prefix}; Fleet will not edit it.")
        if inside:
            owned_lines.append(index)
    return lines, owned_lines


TOP_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def in_strings(lines):
    """Return the indexes of lines inside a multi-line string, after its opening line."""
    inside, quote = set(), None
    for index, line in enumerate(lines):
        if quote:
            inside.add(index)
            if quote in line:
                quote = None
            continue
        quote = next((mark for mark in ('"""', "'''") if line.count(mark) % 2), None)
    return inside


def toml_top_key(text, entry, element):
    """Rewrite one top-level TOML key. It sits before the first table. Keep every other byte."""
    lines = text.splitlines(keepends=True)
    inside = in_strings(lines)
    first = next(
        (
            index
            for index, line in enumerate(lines)
            if index not in inside and TABLE_HEADER.match(line)
        ),
        len(lines),
    )
    start = next(
        (
            index
            for index in range(first)
            if index not in inside
            and (match := TOP_KEY.match(lines[index]))
            and match.group(1) == entry.name
        ),
        None,
    )
    if start is not None:
        end = start + 1
        opener = lines[start].split("=", 1)[1].lstrip()
        for quote in ('"""', "'''"):
            if opener.startswith(quote) and opener.count(quote) == 1:
                # A multi-line string runs to its closing quotes.
                while end < len(lines) and quote not in lines[end]:
                    end += 1
                end += 1
        del lines[start:end]
        first -= end - start
    if element is not None:
        position = start
        if position is None:
            # Add the key after the last top-level line, before any blank lines and tables.
            position = first
            while position > 0 and not lines[position - 1].strip():
                position -= 1
            if position and not lines[position - 1].endswith("\n"):
                lines[position - 1] += "\n"
        lines.insert(position, f"{entry.name} = {value(element)}\n")
    return "".join(lines)


def toml_edit(text, entry, element):
    """Rewrite one named TOML table at the end of the file. Keep every other byte."""
    if not entry.container and not isinstance(element, dict):
        return toml_top_key(text, entry, element)
    lines, owned_lines = toml_block(text, entry.container, entry.name)
    removed = "".join(lines[index] for index in owned_lines)
    quoted = re.sub(r'"(?:\\.|[^"\\])*"|\'[^\']*\'', '""', removed)
    if "#" in quoted:
        raise ValueError(
            f"Comments inside {'.'.join(entry.container + (entry.name,))} would be lost; "
            "move them outside that table."
        )
    kept = [line for index, line in enumerate(lines) if index not in set(owned_lines)]
    result = "".join(kept)
    before = tomllib.loads(text) if text.strip() else {}
    if lookup(before, entry.container + (entry.name,)) is not None and not owned_lines:
        raise ValueError(
            f"{'.'.join(entry.container + (entry.name,))} uses an inline or dotted form; "
            "Fleet edits only its own [table] form."
        )
    if element is not None:
        if result and not result.endswith("\n"):
            result += "\n"
        if result.strip():
            result = result.rstrip("\n") + "\n\n"
        result += f"[{'.'.join(entry.container + (entry.name,))}]\n"
        result += "".join(f"{key} = {value(item)}\n" for key, item in element.items())
    return result


def compact(data):
    """Drop tables that hold nothing, at any depth."""
    if not isinstance(data, dict):
        return data
    result = {key: compact(item) for key, item in data.items()}
    return {key: item for key, item in result.items() if item != {}}


def edited_text(home, relative, fmt, edits):
    """Return (text, mode) after Fleet's edits, or None when the file stays as it is.

    Change only Fleet's elements, then parse the result and require every other value to match.
    """
    text, data = read_shared(home, relative, fmt)
    expected = copy.deepcopy(data)
    for entry, element in edits:
        set_element(expected, entry, element)
        if element is None and fmt == "json":
            prune(expected, entry.container)
    if expected == data and text is not None:
        return None
    if text is None and not expected:
        return None
    if fmt == "json":
        result = json.dumps(expected, indent=2) + "\n"
        reparsed = json.loads(result)
    else:
        result = text or ""
        for entry, element in edits:
            result = toml_edit(result, entry, element)
        reparsed = tomllib.loads(result)
    if fmt == "toml":
        # Removing Fleet's last table also removes its parent table from the text.
        reparsed, expected = compact(reparsed), compact(expected)
    if reparsed != expected:
        raise ValueError(f"Editing ~/{relative} would change other values; nothing was written.")
    path = home / relative
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    return result, mode


def plan(home, checkout, profile, table, catalog, managed=frozenset()):
    """Compare desired outputs with the inventory and home. Write nothing."""
    agents = rendered_agents(home, profile, catalog)
    result = Planner(home, checkout, profile, table, catalog).build(agents)
    inventory = load_inventory(home)
    result.previous = inventory
    recorded_paths = inventory.get("paths", {})
    off_agents = {name for name, enabled in profile.get("agents", {}).items() if not enabled} | {
        name for name, spec in catalog.items() if not spec.get("render", False)
    }
    held_paths = set()

    for relative, output in list(result.outputs.items()):
        if relative in managed:
            raise ValueError(f"Content also manages ~/{relative}; two sources cannot own it.")
        path = home / relative
        current = fingerprint(path)
        saved = recorded_paths.get(relative, {}).get("fingerprint")
        if relative.startswith(TREE + "/"):
            if saved is not None and current not in (None, saved, output.fingerprint()):
                # Refresh replaces an edited copy. The run backup keeps the edited file.
                result.notes.append(f"Replaced edited ~/{relative}; the backup keeps your copy.")
            continue
        folder = path.is_dir() and not path.is_symlink()
        if not folder and (current is None or current == output.fingerprint()):
            continue
        if saved is None or not matches(path, saved) or folder:
            # The user's own file wins. Fleet stops managing this path and writes nothing.
            del result.outputs[relative]
            held_paths.add(relative)
            if saved is not None:
                result.released.append(relative)
            agent = output.agent or "agents"
            result.steps.append(
                f"{agent}: ~/{relative} holds your own file; Fleet leaves it and stopped managing it."
            )

    for relative, record in recorded_paths.items():
        if relative in result.outputs or relative in held_paths:
            continue
        if record.get("agent") in off_agents and record.get("item") != "legacy":
            continue  # Turning an agent off keeps its outputs.
        path = home / relative
        if not path.exists() and not path.is_symlink():
            result.released.append(relative)
        elif (
            any(relative == entry.file for entry in result.entries)
            and record.get("item") == "legacy"
        ):
            result.released.append(relative)  # The earlier whole file becomes a shared file.
        elif matches(path, record["fingerprint"]):
            result.stale_paths[relative] = record
        else:
            result.kept.append(f"~/{relative} changed after Fleet wrote it; kept and released.")
            result.released.append(relative)

    planned = {entry.key(): entry for entry in result.entries}
    previous = {entry_from(item).key(): entry_from(item) for item in inventory.get("entries", [])}
    files = {}
    user_held = []
    for entry in result.entries:
        if entry.file in managed:
            raise ValueError(f"Content also manages ~/{entry.file}; two sources cannot own it.")
        _, data = read_shared(home, entry.file, entry.format)
        found, current = owned(lookup(data, entry.container) or {}, entry)
        old = previous.get(entry.key())
        if not found:
            files.setdefault(entry.file, []).append((entry, copy.deepcopy(entry.value)))
        elif old is None or not same(current, old.value):
            # A value Fleet did not write, or one the user changed, stays the user's.
            # Fleet never takes over an equal value either, so it records nothing for it.
            user_held.append(entry)
            if old is not None:
                result.released_entries.append(old)
            result.steps.append(
                f"{entry.agent}: {entry.item} in ~/{entry.file} holds your own value;"
                " Fleet leaves it and stopped managing it."
            )
        else:
            merged = merge(current, entry.value, old.value)
            if merged != current:
                files.setdefault(entry.file, []).append((entry, merged))
    for key, old in previous.items():
        if key in planned:
            continue
        if old.agent in off_agents:
            continue
        exception = agent_table.exceptions(profile).get(old.item)
        _, data = read_shared(home, old.file, old.format)
        found, current = owned(lookup(data, old.container) or {}, old)
        if not found:
            continue
        if exception == "local":
            result.notes.append(f"{old.agent}: {old.item} released to this machine's own value.")
            result.released_entries.append(old)
        elif same(current, old.value):
            result.stale_entries.append(old)
            files.setdefault(old.file, []).append((old, None))
        else:
            result.kept.append(
                f"{old.agent}: {old.item} in ~/{old.file} changed after Fleet wrote it; kept."
            )
    # A plugin the user already lists is the user's whole: Fleet writes none of its entries.
    plugins = {(e.agent, e.item) for e in user_held if e.item.startswith("plugins.")}
    held = {entry.key() for entry in user_held}
    held |= {entry.key() for entry in result.entries if (entry.agent, entry.item) in plugins}
    result.entries = [entry for entry in result.entries if entry.key() not in held]
    for relative in list(files):
        files[relative] = [edit for edit in files[relative] if edit[0].key() not in held]
        if not files[relative]:
            del files[relative]
    # A held plugin installs nothing, so its install step is not printed.
    result.steps += [step for key, step in result.plugin_steps.items() if key not in plugins]
    plan_plugins(result, inventory, profile, off_agents, home, catalog)
    for relative, edits in files.items():
        outcome = edited_text(home, relative, edits[0][0].format, edits)
        if outcome is not None:
            after = {"sha256": hashlib.sha256(outcome[0].encode()).hexdigest(), "mode": outcome[1]}
            result.files[relative] = (fingerprint(home / relative), after, outcome[0])
    # Print a step in the run that first renders its item. Hook trust follows each hook change.
    hooks_file = catalog.get("codex", {}).get("hooks_file")
    trust = [step for step in result.steps if step.startswith("codex: trust")]
    others = list(dict.fromkeys(step for step in result.steps if step not in trust))
    reported = set(inventory.get("reported", []))
    result.reported = others
    result.steps = [step for step in others if step not in reported]
    if trust and hooks_file in result.files:
        result.steps.append(trust[0])
    result.notes = list(dict.fromkeys(result.notes))
    return result


def plugin_key(record):
    return (record["agent"], record["item"])


def installed_by_user(home, catalog, record):
    """Read the agent's own plugin records on disk. Run no agent command.

    Codex lists an installed plugin as a [plugins."ID"] table in its config.
    Gemini CLI keeps each installed extension in a folder named for it.
    """
    if record["agent"] == "codex":
        relative = (catalog or {}).get("codex", {}).get("mcp_file", ".codex/config.toml")
        try:
            _, data = read_shared(home, relative, "toml")
        except ValueError:
            return False
        plugins = data.get("plugins")
        return isinstance(plugins, dict) and record["id"] in plugins
    if record["agent"] == "gemini":
        return (home / ".gemini/extensions" / extension_name(record["id"])).is_dir()
    return False


def local_source(source):
    """A local source starts with /, ~, or ., or names an existing folder."""
    return source.startswith(("/", "~", ".")) or Path(source).expanduser().is_dir()


def plan_plugins(result, inventory, profile, off_agents, home=None, catalog=None):
    """Compare desired plugins with recorded installs. Install, replace, or remove.

    A plugin the user installed before Fleet declared it stays the user's: no install, no record.
    """
    recorded = {plugin_key(record): record for record in inventory.get("plugins", [])}
    desired = {plugin_key(record): record for record in result.plugins}
    exceptions = agent_table.exceptions(profile)
    for key, record in desired.items():
        old = recorded.get(key)
        if old and (old["id"], old["marketplace"]) == (record["id"], record["marketplace"]):
            continue
        if old is None and home is not None and installed_by_user(home, catalog, record):
            result.steps.append(
                f"{record['agent']}: plugin {record['id']} is already installed; it stays yours,"
                " and Fleet records nothing."
            )
            continue
        if old is None and record["agent"] == "gemini" and local_source(record["id"]):
            # Gemini CLI asks about folder trust for a local source, so an unattended install fails.
            result.notes.append(
                f"gemini: install {record['id']} yourself: gemini extensions install {record['id']}"
            )
            continue
        if old:
            result.plugin_removals.append(old)
        result.plugin_installs.append(record)
    for key, old in recorded.items():
        if old["agent"] in LISTED:
            # Earlier releases ran this agent's installer. Keep that install; the listing owns it now.
            result.released_plugins.append(old)
            result.notes.append(
                f"{old['agent']}: {old['item']} now follows the agent's own plugin listing."
            )
            continue
        if key in desired or old["agent"] in off_agents:
            continue  # Turning an agent off keeps its plugins.
        if exceptions.get(old["item"]) == "local":
            result.released_plugins.append(old)
            result.notes.append(
                f"{old['agent']}: {old['item']} stays installed; Fleet released it."
            )
        else:
            result.plugin_removals.append(old)


def plugin_software(result):
    """Describe plugin actions as software rows for the run account."""
    rows = []
    for action, records in (
        ("removed", result.plugin_removals),
        ("installed", result.plugin_installs),
    ):
        for record in records:
            rows.append(
                {
                    "name": f"{record['agent']} plugin {record['item'].removeprefix('plugins.')}",
                    "version": record["id"],
                    "old": None,
                    "path": f"{record['agent']}'s own plugin installer",
                    "action": action,
                    "plugin": True,
                }
            )
    return rows


def run_installer(arguments):
    """Run one agent installer command. Fleet decided; the agent never reads Fleet's input."""
    result = subprocess.run(arguments, stdin=subprocess.DEVNULL, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()[-3:]
        raise RuntimeError(f"{' '.join(arguments)} failed: " + " ".join(detail))
    return result


def remove_opencode_plugin(home, catalog, module):
    """OpenCode has no uninstall command. Remove the module from its plugin list."""
    relative = catalog["opencode"]["mcp_file"]
    _, data = read_shared(home, relative, "json")
    plugins = data.get("plugin", [])
    kept = [name for name in plugins if name != module and not str(name).startswith(module + "@")]
    if kept == plugins:
        return
    data["plugin"] = kept
    if not kept:
        del data["plugin"]
    path = home / relative
    atomic(path, json.dumps(data, indent=2) + "\n", path.stat().st_mode & 0o777)


def apply_plugins(home, result, catalog, inventory, account=None):
    """Run each planned uninstall, then each install. Record every success at once."""
    released = {plugin_key(old) for old in result.released_plugins}
    inventory["plugins"] = [
        record for record in inventory.get("plugins", []) if plugin_key(record) not in released
    ]
    save_inventory(home, inventory)
    for record in result.plugin_removals:
        command = shutil.which(catalog[record["agent"]]["command"])
        commands = PLUGIN_COMMANDS[record["agent"]]
        if record["agent"] == "opencode":
            remove_opencode_plugin(home, catalog, record["id"])
        elif command:
            run_installer(commands["uninstall"](command, record["id"]))
        else:
            raise RuntimeError(f"{record['agent']} is not on PATH; cannot remove {record['item']}.")
        inventory["plugins"] = [item for item in inventory["plugins"] if item != record]
        save_inventory(home, inventory)
        if account:
            account.applied_software.add(plugin_software(Plan(plugin_removals=[record]))[0]["name"])
    for record in result.plugin_installs:
        command = shutil.which(catalog[record["agent"]]["command"])
        if not command:
            raise RuntimeError(
                f"{record['agent']} is not on PATH; cannot install {record['item']}."
            )
        commands = PLUGIN_COMMANDS[record["agent"]]
        if record["marketplace"] and "marketplace" in commands:
            run_installer(commands["marketplace"](command, record["marketplace"]))
        run_installer(commands["install"](command, record["id"]))
        inventory["plugins"] = [
            item for item in inventory["plugins"] if plugin_key(item) != plugin_key(record)
        ] + [record]
        save_inventory(home, inventory)
        if account:
            account.applied_software.add(plugin_software(Plan(plugin_installs=[record]))[0]["name"])


def uninstall_plugins_plan(home, profile):
    """Return the plugins that fleet uninstall removes: installs that Fleet recorded.

    Listing agents need no command; removing Fleet's listing entry uninstalls them.
    A plugin with a local exception is released, so it stays.
    """
    exceptions = agent_table.exceptions(profile)
    return [
        record
        for record in load_inventory(home).get("plugins", [])
        if record["agent"] not in LISTED and exceptions.get(record["item"]) != "local"
    ]


def uninstall_plugins(records, catalog, account=None):
    """Run each agent's own uninstall. A failure prints one line; the uninstall continues."""
    for record in records:
        name = plugin_software(Plan(plugin_removals=[record]))[0]["name"]
        command = shutil.which(catalog[record["agent"]]["command"])
        if not command:
            print(f"{name} stays: {catalog[record['agent']]['command']} is not on PATH.")
            continue
        try:
            run_installer(PLUGIN_COMMANDS[record["agent"]]["uninstall"](command, record["id"]))
        except (OSError, RuntimeError) as error:
            print(f"{name} stays: {error}")
            continue
        if account:
            account.applied_software.add(name)


def drift(home, profile):
    """List recorded outputs and entries that differ from their records or are missing.

    Read local files only. Outputs of agents that are off stay recorded but never count.
    """
    inventory = load_inventory(home)
    off = {name for name, enabled in profile.get("agents", {}).items() if not enabled}
    lines = []
    for relative, record in sorted(inventory.get("paths", {}).items()):
        if record.get("item") == "legacy" or record.get("agent") in off:
            continue
        if not matches(home / relative, record["fingerprint"]):
            lines.append(f"~/{relative}")
    for item in inventory.get("entries", []):
        entry = entry_from(item)
        if entry.agent in off:
            continue
        try:
            _, data = read_shared(home, entry.file, entry.format)
        except ValueError:
            lines.append(f"~/{entry.file} cannot be read")
            continue
        found, current = owned(lookup(data, entry.container) or {}, entry)
        if not found or not same(current, entry.value):
            lines.append(f"{entry.agent} {entry.item} in ~/{entry.file}")
    return lines


def require_no_conflicts(result, display="fleet"):
    if result.conflicts:
        raise ValueError(
            "Agent outputs conflict with existing values. Nothing was written:\n- "
            + "\n- ".join(result.conflicts)
            + f"\nKeep a value with {display} agents set --name ITEM --state local,"
            + " or move it aside. Then run the update again."
        )


def apply(home, result, catalog=None, account=None):
    """Write the tree, pointers, and owned entries, then run plugin installers.

    Record intent before the first write.
    """
    inventory = {
        "schema_version": SCHEMA,
        "paths": {
            relative: record
            for relative, record in result.previous.get("paths", {}).items()
            if relative not in result.released
        },
        "entries": [
            item
            for item in result.previous.get("entries", [])
            if entry_from(item).key() not in {entry.key() for entry in result.stale_entries}
        ],
        "plugins": list(result.previous.get("plugins", [])),
        "reported": result.reported,
    }
    if not (result.outputs or result.entries or result.previous.get("paths")) and not (
        result.previous.get("entries") or (home / INVENTORY).exists()
    ):
        return  # Nothing to record: keep machines without agent outputs free of an inventory.
    for relative, output in result.outputs.items():
        inventory["paths"][relative] = {
            "agent": output.agent,
            "item": output.item,
            "fingerprint": output.fingerprint(),
        }
    planned = {entry.key() for entry in result.entries}
    planned |= {entry.key() for entry in result.released_entries}
    inventory["entries"] = [
        item for item in inventory["entries"] if entry_from(item).key() not in planned
    ] + [entry.record() for entry in result.entries]
    save_inventory(home, inventory)
    for relative, output in sorted(result.outputs.items()):
        path = home / relative
        if output.link is not None:
            if path.is_symlink() and os.readlink(path) == output.link:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.is_file():
                path.unlink()
            path.symlink_to(output.link, target_is_directory=True)
        elif fingerprint(path) != output.fingerprint():
            if path.is_symlink():
                path.unlink()
            atomic(path, output.data, output.mode)
    for relative in sorted(result.files):
        pending = pending_edits(home, result, relative)
        outcome = edited_text(home, relative, pending[0][0].format, pending) if pending else None
        if outcome is not None:
            atomic(home / relative, outcome[0], outcome[1])
    for relative in sorted(result.stale_paths):
        path = home / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        del inventory["paths"][relative]
    remove_empty_folders(home / TREE)
    save_inventory(home, inventory)
    if result.plugin_installs or result.plugin_removals or result.released_plugins:
        apply_plugins(home, result, catalog, inventory, account)


def pending_edits(home, result, relative):
    """Recompute the edits for one file from the reviewed plan."""
    previous = {
        entry_from(item).key(): entry_from(item) for item in result.previous.get("entries", [])
    }
    edits = []
    for entry in result.entries:
        if entry.file != relative:
            continue
        _, data = read_shared(home, relative, entry.format)
        found, current = owned(lookup(data, entry.container) or {}, entry)
        old = previous.get(entry.key())
        if not found:
            edits.append((entry, copy.deepcopy(entry.value)))
        elif old is not None:
            edits.append((entry, merge(current, entry.value, old.value)))
    edits += [(entry, None) for entry in result.stale_entries if entry.file == relative]
    return edits


def remove_empty_folders(root):
    if not root.is_dir() or root.is_symlink():
        return
    for folder in sorted(
        (path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()), reverse=True
    ):
        if not any(folder.iterdir()):
            folder.rmdir()


def save_inventory(home, inventory):
    atomic(home / INVENTORY, json.dumps(inventory, indent=2, sort_keys=True) + "\n")


def removal_operations(home, workspace):
    """Plan uninstall: remove owned paths and Fleet's entries. Never delete a shared file.

    Return (paths, shared) where shared maps a file to a temporary copy without Fleet's entries.
    """
    inventory = load_inventory(home)
    paths = set(inventory.get("paths", {}))
    shared = {}
    grouped = {}
    for item in inventory.get("entries", []):
        entry = entry_from(item)
        grouped.setdefault(entry.file, []).append((entry, None))
    for relative, edits in grouped.items():
        outcome = edited_text(home, relative, edits[0][0].format, edits)
        if outcome is not None:
            target = Path(workspace) / ("shared-" + hashlib.sha256(relative.encode()).hexdigest())
            atomic(target, outcome[0], outcome[1])
            shared[relative] = target
    return paths, shared


def restore_shared(home, relative, saved, workspace, inventory):
    """Restore only Fleet's entries in a shared file from a backup copy.

    Keep every other value that the agent or user wrote after the backup.
    """
    fmt = "toml" if relative.endswith(".toml") else "json"
    old_text = saved.read_text() if saved and saved.is_file() else ""
    old = (
        (json.loads(old_text) if fmt == "json" else tomllib.loads(old_text))
        if old_text.strip()
        else {}
    )
    edits = []
    for item in inventory.get("entries", []):
        entry = entry_from(item)
        if entry.file != relative:
            continue
        found, element = owned(lookup(old, entry.container) or {}, entry)
        edits.append((entry, copy.deepcopy(element) if found else None))
    outcome = edited_text(home, relative, fmt, edits)
    if outcome is None:
        return home / relative if (home / relative).exists() else None
    target = Path(workspace) / ("restore-" + hashlib.sha256(relative.encode()).hexdigest())
    atomic(target, outcome[0], outcome[1])
    return target


def shared_files(home):
    return {item["file"] for item in load_inventory(home).get("entries", [])}


def describe(result):
    """Return review lines for the agent plan."""
    lines = [f"Agent output: ~/{name}" for name in sorted(result.outputs)]
    lines += [
        f"Agent entry: {entry.agent} {entry.item} in ~/{entry.file}" for entry in result.entries
    ]
    lines += [f"Remove stale agent output: ~/{name}" for name in sorted(result.stale_paths)]
    lines += [
        f"Remove stale agent entry: {entry.item} in ~/{entry.file}"
        for entry in result.stale_entries
    ]
    for row in plugin_software(result):
        verb = "Install" if row["action"] == "installed" else "Remove"
        lines.append(f"{verb} plugin: {row['name']} ({row['version']})")
    return lines + result.kept + result.notes + result.steps


def main():
    from common import read_toml
    from style import ArgumentParser

    parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--home", type=Path, default=Path.home(), help="full target home path")
    args = parser.parse_args()
    home = args.home
    profile = read_toml(home / ".fleet/profile.toml")
    catalog = read_toml(ROOT / "agents/manifest.toml")["agents"]
    checkout = dotfiles_library.active(home)
    from convert import content_manifest

    try:
        table = agent_table.load(checkout, content_manifest(checkout), catalog)
        result = plan(home, checkout, profile, table, catalog)
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}")
        return 1
    for line in table.warnings + describe(result):
        print(line)
    for line in result.conflicts:
        print("Conflict: " + line)
    return 1 if result.conflicts else 0


if __name__ == "__main__":
    import sys

    from style import run_cli

    sys.exit(run_cli(main, ROOT))
