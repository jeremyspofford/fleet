# Layer 2 agent module

Status: ruled design, now built. The owner ruled on all thirteen open questions and changed parts of the design during review.
Sections 1 to 10 record that ruled design as written on 2026-11-17. The build on `main` followed it in most points.
The Current state section describes `main` today. Section 11 marks each open point as built, built differently, or not built.

Fleet is Layer 1: the manager for machine enrollment and later changes. Dotfiles is Layer 0: independent configuration content.
Layer 2 adds shared modules that the manager applies. The agent module is the first Layer 2 build.
A ruling is the owner's decision on one open question. Section 6 records all thirteen rulings.

## Terms

- An agent is an installed coding assistant, such as Claude Code or Codex.
- A skill is a folder of instructions for an agent. Its main file is `SKILL.md`.
- An instruction file is a file of standing instructions that an agent reads in every session, such as `AGENTS.md`.
- A rule set is one text file of standing instructions that content declares.
- An include line is a line in an instruction file that makes the agent read another file.
- MCP means Model Context Protocol, a standard that lets an agent call external tools.
- An MCP server is a program or web service that offers those tools. In this document, a server means an MCP server.
- A plugin is a package that an agent installs to add skills, commands, hooks, or servers.
- An extension is Gemini CLI's plugin type.
- A connector is a hosted service that an agent reaches after the user signs in.
- A hook is a command that an agent runs when a named event occurs.
- A provider is a service that supplies models to an agent. OpenRouter is one.
- An item is one declared skill limit, rule set, server, connector, plugin, or hook.
- The manifest is `fleet.toml` in the content repository. TOML is a configuration file format.
- The profile is `~/.fleet/profile.toml`. It saves this machine's choices.
- The manager store is `~/.fleet`. It holds the manager's own records.
- The agent catalog is `agents/manifest.toml` in the manager. It lists each agent's detection commands, formats, and paths.
- The inventory is `~/.fleet/agents.json`. It records every output that Fleet rendered. The rulings call it the record.
- A fingerprint records a file's content hash and permissions, or a link's target.
- The payload is the full agent configuration that one machine receives.
- The tree is the Fleet-owned folder that holds the payload.
- A pointer is one link, line, or entry that leads an agent to the tree.
- An owned entry is one named value that Fleet writes inside a file that the agent or user also writes.
- Entry-level rendering writes an item itself into an agent's file, as an owned entry. A pointer writes only a reference.
- A self-contained folder is one folder that an agent loads as a unit, such as a plugin folder.
- The gateway is one Fleet MCP server that stands between each agent and every declared server.
- A bridge plugin is a Fleet-shipped plugin that lets one agent run declared hooks.
- The foundation is the tracked base that Fleet ships: the tracked content that Fleet installs, plus the manifest.
- A marketplace is a Git repository that offers packages of items. That name is still open.
- A pulled-in component is a package that a machine takes from a marketplace.
- The library is a folder on the machine that holds pulled-in components. Git does not track it.
- Unmanaged content is content that Fleet neither installs nor records.
- Detect-only means that Fleet detects an agent and renders nothing for it.
- An exception is one machine's choice to turn an item off or to keep its own value.
- Drift means a rendered output differs from its record or is missing.
- A conflict is a hand edit to a managed file that an update also changes.
- A domain selects optional content. Content declares its domains. Each machine carries one domain.
- Core is the shared content that every machine receives.
- A group selects optional content that one machine can turn on or off.
- chezmoi is the tool that applies managed files. Mise is the tool that selects tool versions.
- The wizard is the guided enrollment that runs in a terminal.
- The run account is the report that a change-making run prints and saves.
- The update gate is the check that `fleet update` runs before any backup.
- Dispatch is Fleet's existing way to run one command on another machine.
- The builder is whoever implements this design.
- The test machine is the Linux machine that the author used for the tests in Appendix B.

## Design in brief

The deployment model is the primary shape of this design:

1. Content declares agent configuration once, in a neutral `agents` table.
2. Fleet deploys one payload per machine into a tree that Fleet owns.
3. Each agent gets pointers into that tree. Fleet sets each pointer once.
4. A later update changes the tree, and only the tree.
5. Entry-level rendering remains only for an agent that accepts no pointer.

The foundation model sets ownership in three layers:

1. The foundation is the tracked base that Fleet ships. It is the only conflict surface.
2. The library holds components that a machine pulls in. Git does not track it.
3. The user's own files stay as they are. An update never rewrites them.

The build has four phases:

| Phase | Scope |
| --- | --- |
| 1 | The module core: the table, the tree, pointers, exceptions, plugins, and the agent catalog changes. |
| 2 | Marketplaces. |
| 3 | The gateway. |
| 4 | A read-only import, and a design study for bridge plugins. |

Section 2 defines the deployment model. Section 3 defines the foundation model.
Sections 7, 8, and 9 cover marketplaces, the gateway, and remote profile management.
Section 10 lists each place where a ruling meets the evidence. Section 11 lists what stays open.

## Purpose

Each agent keeps its settings in its own files and formats.
A user who changes agents must rebuild skills, rules, servers, plugins, and hooks by hand.
This module keeps one store of agent configuration in a neutral format. A neutral format names no agent's file layout.
Fleet renders that store. To render means to deliver each neutral declaration in a form that one agent reads.
A user can then switch agents and keep the same setup.

The module does not manage models, permissions, credentials, project settings, or agent installation.
It also carries no provider entries and no whole instruction files. Section 1 states both limits.

## Rules this design honors

The owner settled three rules for this module:

1. Dependencies point one way. Content declares configuration. The profile records machine-local selections. Profile data never enters the content repository.
2. Generated files are listed and regenerable. Fleet leaves no orphan: a generated file that no record lists.
3. Fleet never depends on Nova, the owner's separate agent project. Nova can consume the same files later.

The module also keeps these existing manager rules:

- Review planned changes before interactive apply. Back up every target before replacement.
- Never choose overwrite for an unattended conflict.
- Declare secret references and names, never credentials.
- Install no agent and start no agent session. Agent login remains separate.
- Add no custom sync engine and no background synchronization service.

Two rulings change how the manager treats agent commands:

- Fleet never runs an agent's command to write settings (ruling 8).
- Fleet runs an agent's own installer to install or uninstall a plugin (ruling 7). That is the only exception.

Ruling 7 also differs from the rule for tools. A content default never installs a tool. A declared plugin does install.

## Current state

This section describes `main` at commit `2f1e154`, after release 0.10.0. The build of phases 1 to 4 has shipped.
Outside this section, today means Fleet 0.5.0, the release before the build.
[The agent guide](AGENTS.md) describes the built module for users.

Each row names the files that prove it.

| Area | On `main` | Proof |
| --- | --- | --- |
| Agent catalog | Eight agents. Grok and Ollama render nothing. Antigravity receives only the gateway entry. | `agents/manifest.toml`, `scripts/agent_deploy.py` |
| Agent choice | The wizard's sixth step, `agents`, turns each detected agent on or off. | `scripts/enrollment.py` |
| Store | `~/.fleet` holds the manager store, the profile, and the inventory. Older store folders move there. | `scripts/store_migration.py` |
| Profile | `~/.fleet/profile.toml` saves agent choices, the domain, and the exception list under `agent_items`. | `scripts/store_migration.py`, `scripts/agent_table.py` |
| Agents table | `fleet.toml` declares skills, rule sets, servers, connectors, plugins, and hooks. | `scripts/agent_table.py` |
| Skills | Skills are ordinary content at `~/.agents/skills`. Claude Code reads them through a link in its plugin folder. | `scripts/agent_deploy.py` |
| Tree | `~/.fleet/agents` holds the payload. An update replaces a hand edit there, and the backup keeps the edited copy. | `scripts/agent_deploy.py` |
| Pointers | Claude Code and Gemini CLI get linked folders. Codex, OpenCode, Pi, and Antigravity get owned entries. | `scripts/agent_deploy.py` |
| Gateway | Every declared server reaches each agent through one `stdio` entry named `fleet`. | `scripts/gateway.py`, `scripts/agent_deploy.py` |
| Plugins | Codex and Gemini CLI run their installers. Claude Code, OpenCode, and Pi get an entry in their plugin listing. | `scripts/agent_deploy.py` |
| Exceptions | `fleet agents set` sets an item `on`, `off`, or `local`. `fleet agents list` shows each item's state. | `scripts/command_arguments.py`, `scripts/agent_deploy.py` |
| Import | `fleet agents import --agent NAME` prints table entries from one agent's settings. It writes nothing. | `scripts/agent_import.py` |
| Marketplaces | `fleet marketplaces` adds, updates, deletes, and lists marketplaces. Packages sit in `~/.fleet/marketplace-packages`. | `scripts/marketplaces.py`, `scripts/command_arguments.py` |
| Remote changes | Dispatch saves a domain, group, agent, or tool choice on another machine. Its next update applies the choice. | `scripts/remote.py`, `scripts/command.py` |
| Inventory | `~/.fleet/agents.json` lists owned paths, owned entries, and the keys that Fleet wrote. | `scripts/agent_deploy.py` |
| Drift | Status lines include agent outputs that differ from their records. | `scripts/command.py` |
| Conflicts | A differing value at a pointer or an owned entry stops the run before backup. | `scripts/agent_deploy.py`, `scripts/manager.py` |
| Foundation | Uncommitted edits to managed content block an update. Untracked and unmanaged files never block it. | `scripts/updates.py` |

These limits remain on `main`:

1. Codex and Pi receive no rule sets. Fleet reports their rule sets as unsupported.
2. OpenCode and Pi run no declared hooks. Fleet reports those hooks as unsupported. `docs/BRIDGE-SPIKE.md` proposes a fix.
3. Grok and Ollama receive nothing. Antigravity receives the gateway entry only.
4. A conflict offers no choice in the run. The user sets a `local` exception or moves the value aside, then updates again.
5. Fleet does not check whether an agent already holds a declared plugin.

Fleet 0.5.0 shipped a small generator. It wrote one minimal settings file per agent and one link per skill.
Its catalog named seven agents, and wizard step 8 chose them. Unknown or modified outputs stopped it before any write.
That release had six limits that blocked this design. The build removed each one:

| Limit in Fleet 0.5.0 | On `main` |
| --- | --- |
| Settings carried no rules, servers, plugins, connectors, or hooks. | The agents table declares all six kinds. |
| The generator owned whole files, so a hand-written settings file stopped setup. | Fleet owns a tree, pointers, and single entries. |
| The catalog fixed the skills folder name, and skill links pointed into the content checkout. | Skills are ordinary content at `~/.agents/skills`. |
| Status, doctor, and the update gate read chezmoi status only. | Status lines include agent drift. |
| Uninstall deleted every listed path, and rollback rewrote each saved file whole. | Uninstall and restore change only Fleet's paths and entries. |
| Any uncommitted change in the content checkout stopped check and update. | Only uncommitted edits to managed content stop them. |

## 1. Contract addition

### Placement

The module is manager code, not an add-on (ruling 1).
It extends the generator, wizard step, profile field, and inventory that the manager already has.
No add-on host exists yet, so the [add-on contract](ADDON-INTERFACE.md) stays unused here.
Section 4's removal rules follow that contract's removal protections.
The `agents` table lives inside `fleet.toml` (ruling 2). One manifest keeps one validator and one schema.

### Table layout

The content repository declares agent configuration in its manifest, under one table: `agents`.
The table is optional. Without it, content declares no items. Skills still install, because skills are ordinary content.
Fleet 0.5.0 ignores an unknown table. Content can therefore add `agents` before every machine updates.
Authored files stay in the repository: skill folders, rule sets, and hook scripts.
The build documents the table in the [content contract](CONTENT-CONTRACT.md). That document is the reference for exact validation.

| Table or key | Holds |
| --- | --- |
| `agents.schema` | The integer 1. Fleet rejects a number that it does not know. |
| `agents.skills.NAME` | Optional domain limits for one skill. |
| `agents.rules.NAME` | One rule set. |
| `agents.mcp.NAME` | One MCP server. |
| `agents.connectors.NAME` | One connector. |
| `agents.plugins.NAME` | One plugin, with one source per agent. |
| `agents.hooks.NAME` | One hook. |

Names use lowercase letters, digits, and single hyphens. A name is unique within its table.
Unknown tables and unknown keys fail validation. A new key needs a new schema number.
An item key joins the table name and the item name, such as `mcp.docs-search`.

### Shared keys

Rule sets, servers, connectors, plugins, and hooks accept these keys:

| Key | Required | Meaning |
| --- | --- | --- |
| `description` | Yes | One plain sentence. The wizard and reports show it. |
| `agents` | No | Agent names that receive the item. Default: every selected agent that supports it. |
| `domains` | No | Machine domains that receive the item. Default: every domain. |

An item without `domains` reaches every machine.
A machine receives a limited item when the list holds the machine's own domain.
Each listed domain must be one that the content declares. Section 9 states the domain rules.
As built from the first draft, `core` in a list selects the machines whose own domain is core.
Section 11, item 4 asks whether a list may name `core`.

### Skills

Skills are ordinary content (ruling 3). The repository stores them under their real home name: `.agents/skills`.
Each direct child folder that holds `SKILL.md` is one skill. The folder name is the skill name.
chezmoi installs skills at `~/.agents/skills`, as it installs any other content file.
The existing file commands then list, back up, and repair them.
Skills follow the [Agent Skills specification](https://agentskills.io/specification).
That specification requires a `name` and a `description` at the top of `SKILL.md`. The name must match the folder name.
Fleet checks the folder name and the file's presence. Each agent validates the rest.
Fleet never rewrites a skill.
A skill needs no manifest entry. Add `agents.skills.NAME` only to limit its domains.
A skill entry accepts `domains` only. A skill reaches every agent that reads its folder, so `agents` cannot limit it.
Fleet leaves out a skill that this machine's domain or exception list excludes.
This layout has two costs. An edit reaches agents only after commit and update.
A skill's domain limit needs new converter code.

### Rule sets

A rule set is one Markdown text file of standing instructions.
Whole instruction files stay out of this module (ruling 13). Plain content installs a file such as `~/.codex/AGENTS.md`.
A declared rule set is an item. It adds one key to the shared keys:

| Key | Meaning |
| --- | --- |
| `file` | A Markdown file in the repository, relative to its root. |

Fleet copies each rule set into the tree. It never edits the text.
An agent's rule pointer then leads to the rule sets that the agent receives. Section 2 lists each agent's pointer.

### MCP servers

| Key | Applies to | Meaning |
| --- | --- | --- |
| `transport` | All | `stdio` starts a local program. `http` calls a web address. |
| `command` | `stdio` | The program to start. |
| `args` | `stdio` | A list of arguments. |
| `env` | `stdio` | Variable names with plain, non-secret values. |
| `pass_env` | `stdio` | Names of variables that the server reads from the user's environment. |
| `url` | `http` | The server address. |
| `headers` | `http` | Header names with plain, non-secret values. |
| `header_env` | `http` | Header names mapped to variable names. |
| `bearer_token_env` | `http` | The name of the variable that holds a bearer token. |

A bearer token is a secret string that proves access. The manifest holds variable names, never secret values.
`pass_env`, `header_env`, and `bearer_token_env` name variables in the user's environment.
The existing `secrets` table can supply those variables through `~/.secrets`.
Fleet renders each name as the agent's own variable reference. Fleet never writes the value.
Fleet scans `env` and `headers` values with the secret patterns from the wizard's home scan. A match fails validation.
Schema 1 omits the older Server-Sent Events transport. Pi rejects that transport.

### Connectors

A connector entry names a hosted service and its optional `url`.
With a `url`, Fleet renders the address as an HTTP server without credentials. The user signs in through the agent.
Without a `url`, Fleet cannot render the connector. Fleet reports it as a manual step.
Each agent keeps the sign-in session in its own store. Fleet never reads that store.

### Plugins

No shared plugin format exists. Each agent installs its own package type.
A plugin entry is a cross-reference: one name, one description, and one source per agent.
`sources` maps an agent name to an `id` and an optional `marketplace`.
That key names the agent's own plugin marketplace: a catalog that the agent installs plugins from.
It is not the marketplace of section 7.
Fleet treats both values as opaque text in that agent's own notation.
Plugins are fully managed (ruling 7). Fleet installs a declared plugin through the agent's own installer.
Fleet uninstalls it the same way when the declaration stops selecting this machine. The inventory drives both actions.
Section 2 lists each agent's installer commands. Section 4 sets the rules for both actions.

### Hooks

| Key | Meaning |
| --- | --- |
| `event` | When the hook runs. Schema 1 defines six events. |
| `tools` | Optional tool classes for tool events: `shell`, `edit`, `read`, or `mcp`. Default: all tools. |
| `command` | An executable file in the repository, relative to its root. |
| `args` | An optional list of arguments. |
| `timeout` | An optional time limit in seconds. |

The six events are `session-start`, `session-end`, `prompt-submit`, `before-tool`, `after-tool`, and `stop`.
The agent catalog maps each event and tool class to an agent's native name.
Fleet copies each hook script into the tree. An agent's hook entry names that copy, never the checkout.
Each agent sends its own event data and reads its own exit codes. Fleet translates neither.
Fleet sets three variables for the command: `FLEET_AGENT`, `FLEET_HOOK`, and `FLEET_HOOK_EVENT`.
They hold the agent name, the item name, and the neutral event. One script can then serve several agents.
The builder chooses how to set them: a command prefix or a small generated wrapper in the tree.
An agent that lacks an event skips that hook. Fleet reports the skip.
OpenCode and Pi run hooks only through code. The first build reports their hooks as unsupported (ruling 10).
Hooks are the least portable part. Start with hooks that only read event data.
Hooks and `stdio` servers run commands from content. Use only trusted repositories.
The content contract gives the same warning for templates.

### What the table leaves out

- Provider entries are out. OpenRouter and API-key providers are not agents, so they get no item kind.
- A user sets providers inside OpenCode and Pi by hand. Those settings stay unmanaged.
- Whole instruction files are out (ruling 13). A rule set is the item kind for shared instructions.
- Models, permissions, credentials, and project settings are out, as the Purpose section states.

### Complete example

```toml
[agents]
schema = 1

[agents.skills.release-notes]
domains = ["work", "client"]

[agents.rules.commit-style]
description = "Write commit messages in the imperative mood."
file = "agents/rules/commit-style.md"

[agents.rules.client-privacy]
description = "Keep client names out of commits and logs."
file = "agents/rules/client-privacy.md"
domains = ["client"]

[agents.mcp.docs-search]
description = "Search the team documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
env = { DOCS_REGION = "us" }
pass_env = ["DOCS_API_KEY"]

[agents.mcp.issues]
description = "Read and update work issues."
transport = "http"
url = "https://mcp.example.com/issues"
bearer_token_env = "ISSUES_TOKEN"
domains = ["work", "client"]

[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
agents = ["claude", "codex"]

[agents.plugins.review-kit]
description = "Review commands and checklists."
domains = ["personal"]

[agents.plugins.review-kit.sources]
claude = { id = "review-kit@example-market", marketplace = "example/agent-plugins" }
codex = { id = "review-kit@example-market", marketplace = "example/agent-plugins" }
gemini = { id = "https://github.com/example/review-kit" }

[agents.hooks.guard-shell]
description = "Stop destructive shell commands."
event = "before-tool"
tools = ["shell"]
command = "agents/hooks/guard-shell.sh"
timeout = 10

[agents.hooks.session-notes]
description = "Print project reminders at session start."
event = "session-start"
command = "agents/hooks/session-notes.sh"
agents = ["claude", "gemini"]
```

The example assumes content that declares the `personal`, `work`, and `client` domains.
The repository then holds these authored files beside the manifest:

```text
.agents/skills/release-notes/SKILL.md
agents/rules/commit-style.md
agents/rules/client-privacy.md
agents/hooks/guard-shell.sh
agents/hooks/session-notes.sh
```

The example names two variables: `DOCS_API_KEY` and `ISSUES_TOKEN`. The existing `secrets` table can declare both:

```toml
[secrets.DOCS_API_KEY]
reference = "op://Example/Docs/api-key"
enabled = false

[secrets.ISSUES_TOKEN]
reference = "op://Example/Issues/token"
enabled = false
```

### Validation

Fleet validates the table before it plans any write. One failure stops the run.

1. Reject unknown tables, unknown keys, wrong types, and bad names.
2. Reject absolute paths and parent traversal. Require Git to track every named file.
3. Reject an entry that lacks a key its transport requires.
4. Reject a secret match in `env` or `headers`.
5. Reject a domain that the content does not declare.
6. Warn about an unknown agent name, then skip it. Content can name an agent that an older manager lacks.

### Machine choices

The manifest never names a machine, a home path, or a native file. Those belong to the profile and the agent catalog.
The profile keeps the machine's choices: agents, domain, groups, and exceptions.
The wizard needs no new step. Step 8 still selects agents. Its review step lists the items that each agent receives.

### Exceptions

The profile holds the machine's exception list (ruling 9).
The ruling names two kinds: an item off, and an item with a local value.
This design reads them as two states. Each entry names one item and one state:

| State | Effect on this machine |
| --- | --- |
| `off` | The machine does not receive the item. Fleet removes what it rendered, under section 4's removal rules. |
| `local` | The machine keeps its own value for the item. Fleet renders nothing for it. |

An owned entry that Fleet wrote earlier stays in place under `local`. It becomes the user's own.
A command edits the list. Every update honors it.
Section 9 lets another machine edit the list through dispatch.
This design proposes two limits for the list (section 11, item 3):

- An exception applies to every agent on the machine.
- The list holds item keys and states, never values. A hand-written value can hold a secret.

The builder names the profile key and the command. This sketch shows the shape:

```toml
[agent_items]
"mcp.issues" = "local"
"hooks.guard-shell" = "off"
```

## 2. Deployment model and render targets

### Deployment model

Fleet deploys one payload per machine: the full agent configuration, in a tree that Fleet owns.

1. An update changes the tree, and only the tree.
2. Each agent gets one pointer to the tree for each kind of item. Fleet sets a pointer once.
3. After the pointer, Fleet does not write inside the agent's own files.
4. Entry-level rendering remains only for an agent that accepts no pointer.

Rulings 4, 5, and 6 govern that remainder. The rulings name four pointers:

| Kind of item | Pointer |
| --- | --- |
| Skills | A link, or a shared folder that the agent already reads. |
| Rule sets | One include line. |
| Hooks | Hook entries that name scripts inside the tree. |
| Servers | One gateway entry, from phase 3. |

Before phase 3, servers have no pointer of their own.
Claude Code and Gemini CLI still need no server entries. Each loads one self-contained folder (ruling 4).
That folder sits in the tree, and one link leads to it.
Codex, OpenCode, and Pi take owned server entries until the gateway arrives.

### The tree

The tree holds what the pointers lead to:

- One copy of each rule set that the machine receives.
- One copy of each hook script that the machine receives.
- One generated folder for each agent that loads a self-contained folder.
- From phase 3, the gateway's list of servers.

Skills sit at `~/.agents/skills`, where chezmoi installs them (ruling 3). That folder is the shared folder for skills.
Fleet builds every tree file from the foundation or the library. The inventory lists each file.
The builder chooses the tree's path and layout. This sketch shows one layout inside the manager store:

```text
~/.fleet/agents/
├── rules/
│   ├── commit-style.md               one copy of each rule set
│   ├── claude.md                     the combined rules that Claude Code receives
│   └── opencode.md                   the combined rules that OpenCode receives
├── hooks/
│   ├── guard-shell.sh
│   └── session-notes.sh
├── claude/                           Claude Code loads this folder as a plugin
│   ├── .claude-plugin/plugin.json
│   ├── .mcp.json
│   └── hooks/hooks.json
└── gemini/                           Gemini CLI loads this folder as an extension
    ├── gemini-extension.json
    ├── hooks/hooks.json
    └── GEMINI.md
```

### Pointers

For an item kind with a pointer, Fleet writes only that pointer in the agent's own folders. The inventory records each pointer.
This table shows each agent's pointers in the first build. The agent sections give the evidence.

| Agent | Skills | Rule sets | Hooks | Servers before phase 3 |
| --- | --- | --- | --- | --- |
| Claude Code | One link per skill | One include line | Inside the linked plugin folder | Inside the linked plugin folder |
| Codex | Shared folder | No pointer: one owned key | One entry per hook | No pointer: owned entries |
| Gemini CLI | Shared folder | Inside the linked extension folder | Inside the linked extension folder | Inside the linked extension folder |
| OpenCode | Shared folder | One settings entry | Unsupported (ruling 10) | No pointer: owned entries |
| Pi | Shared folder | One link | Unsupported (ruling 10) | No pointer: owned entries |
| Antigravity | Detect-only (ruling 12) | Detect-only | Detect-only | Detect-only |
| Grok | Detect-only (ruling 12) | Detect-only | Detect-only | Detect-only |

Three points differ from the ruled pointer list. Section 10 explains each one:

- Among the rendered agents, only Claude Code takes an include line that can reach the tree.
- Claude Code needs one link per skill, so a new skill adds one link.
- A hook entry exists per hook, so a new hook adds one entry.

### Render kinds

A render kind is the way that one item reaches one agent:

1. An owned path is a file, link, or folder that Fleet creates and owns whole. The tree and each link are owned paths.
2. An owned entry is one named value inside a file that the agent or user also writes. An include line is an owned entry.
3. An installed plugin is a plugin that Fleet installed through the agent's own installer (ruling 7).
4. A reported step is an action that only the user or the agent can complete. Fleet prints the exact command and writes nothing.
5. A served item reaches the agent through the gateway at run time. Phase 3 adds this kind.

The first two are the file kinds. Owned paths need no merge and remove cleanly.
Owned entries need the shared-file rules in section 4.
Where an agent accepts no pointer, Fleet prefers a self-contained folder over entries in shared files (ruling 4).
Fleet renders at user level only. It never writes a project folder.
Paths below are the Linux, macOS, and WSL paths. WSL means Windows Subsystem for Linux.
Native Windows paths stay descriptive, as they are today.
The run account lists file outputs under Files, plugin installs under Software, and reported steps under Next steps.
Fleet prints a reported step in the run that first renders or changes its item.

### Evidence labels

Every claim below carries one label:

- Tested: the author ran the named version on 2026-11-17 and observed the behavior. Each run used a throwaway home folder.
- Documented: vendor documentation or vendor source states it. No test confirmed it.
- Unverified: neither a test nor a vendor statement confirms it. The first build must test it.

Tested and Documented claims are known. Unverified claims are not.
Some Tested claims describe the test machine's real settings files. The author read their key names, never their values.
No test started an agent session. A Tested claim shows what an agent's own commands report.
Agents change quickly. Each label describes one version on one date.

### Skills

Several agents read each other's folders. One skill in two folders can reach one agent twice.
Gemini CLI then prints a conflict warning when a command starts (Tested with `gemini skills list`).
Today's agent catalog gives Codex `~/.agents/skills` and Gemini CLI `~/.gemini/skills`. With both on, Gemini CLI finds each skill twice.

| Agent | Own skills folder | Reads `~/.agents/skills` | Reads `~/.claude/skills` | Same name twice |
| --- | --- | --- | --- | --- |
| Claude Code | `~/.claude/skills` | Not documented | Own folder | Personal wins over project (Documented) |
| Codex | `~/.codex/skills`, deprecated | Yes (Documented) | Not documented | Both copies appear (Documented) |
| Gemini CLI | `~/.gemini/skills` | Yes; it overrides the own folder (Tested) | No (Tested) | Warning at start (Tested) |
| Antigravity | `~/.gemini/config/skills` | Not documented | Not documented | Not documented |
| OpenCode | `~/.config/opencode/skills` | Yes (Tested) | Yes (Tested) | Loads one copy (Tested); the winner is not fixed (Documented) |
| Pi | `~/.pi/agent/skills` | Yes (Documented) | No (Documented) | First found wins, with a warning (Documented) |
| Grok | `~/.grok/skills` | Yes (Documented) | Yes, unless switched off (Documented) | Loads one copy per name (Documented) |

chezmoi installs each skill once, at `~/.agents/skills` (ruling 3). Five agents read that folder and need no pointer.
Claude Code documents no such folder. Fleet links each skill into `~/.claude/skills`.
Antigravity does not document it. After a tested install, it gets links in `~/.gemini/config/skills`.
The single shared folder ends Gemini CLI's double listing.
OpenCode and Grok also read Claude Code's folder. They then find a skill twice and load one copy.
The shared folder also reaches an installed agent that is off. Fleet writes nothing into that agent's own folders.
Agent choices cannot limit skills for these five agents.
Fleet never relies on one agent reading another agent's private folder. That reading can change or be switched off.

### Rule pointers

The rulings name one include line for rules. The agents differ:

| Agent | What the agent offers | Evidence |
| --- | --- | --- |
| Claude Code | An include line in `~/.claude/CLAUDE.md` that names any path. | Documented |
| Codex | No include syntax. A settings key holds extra instruction text. | Documented in source |
| Gemini CLI | An include line that cannot leave the including file's folder. An extension folder can carry a context file. | Documented in source; Tested for the extension |
| Antigravity | An include line of its own form, in `~/.gemini/config/AGENTS.md`. | Documented |
| OpenCode | No include syntax. A settings key lists extra instruction files. | Documented |
| Pi | No include syntax. A separate file adds text to the system prompt. | Documented |
| Grok | No include syntax. A rules folder holds extra files. | Documented in source |

This design proposes one combined rule file per agent in the tree, because `agents` can limit a rule set.
A pointer then names one file.
OpenCode reads `~/.claude/CLAUDE.md` when it has no instruction file of its own (Documented).
Grok reads that file by default (Documented in source). Neither agent expands Claude Code's include line.
Each agent therefore gets its own rule pointer.

### Variable references

Each agent has its own way to name an environment variable. Fleet renders `pass_env`, `header_env`, and `bearer_token_env` with it:

| Agent | Reference form | Evidence |
| --- | --- | --- |
| Claude Code | `${NAME}` in `command`, `args`, `env`, `url`, and `headers` | Tested in a plugin folder for `args` |
| Codex | Names in `env_vars`, `bearer_token_env_var`, and `env_http_headers` | Tested |
| Gemini CLI | `$NAME` or `${NAME}` | Tested for `args` |
| Antigravity | None confirmed | Unverified |
| OpenCode | `{env:NAME}` | Tested |
| Pi | `${NAME}` in `env` and `headers` values | Documented; Tested for a bearer header |
| Grok | `${NAME}` in `url`, `command`, `args`, `env`, and `headers` | Documented |

When an agent offers no reference form, Fleet skips that server for that agent and reports why.

### Plugin installers

Plugins are fully managed (ruling 7). Fleet runs each agent's own installer:

| Agent | Install | Uninstall | Runs without a prompt | Evidence |
| --- | --- | --- | --- | --- |
| Claude Code | `claude plugin install ID` | `claude plugin uninstall ID` | Yes, except a plugin that installs through a declared command | Tested for the help text; Documented |
| Codex | `codex plugin add ID` | `codex plugin remove ID` | Yes | Tested for the help text; Documented in source |
| Gemini CLI | `gemini extensions install SOURCE --consent --skip-settings` | `gemini extensions uninstall NAME` | Yes for a Git address. A local path can still ask about folder trust. | Tested for the help text; Documented in source |
| OpenCode | `opencode plugin MODULE --global` | None in version 1.17.8 | Yes for the install | Tested for the help text; Documented in source |
| Pi | `pi install SOURCE` | `pi remove SOURCE` | Yes | Tested for the help text; Documented |
| Antigravity | `agy plugin install NAME` | `agy plugin uninstall NAME` | Unverified | Documented; detect-only |
| Grok | `grok plugin install SOURCE --trust` | `grok plugin uninstall NAME` | Needs `--trust` | Documented in source; detect-only |

The tests read each command's help text. No test installed a plugin.
Claude Code and Codex install from a plugin marketplace. Fleet adds a declared plugin marketplace before the install.
Claude Code's install command adds it through `--marketplace SOURCE` (Tested for the help text).
Codex needs a separate command first: `codex plugin marketplace add SOURCE` (Tested for the help text).
Section 11, item 8 asks whether an install includes that command.
Removing a plugin marketplace uninstalls every plugin that came from it, in Claude Code and in Grok (Documented).
This design therefore proposes that Fleet never removes a plugin marketplace.
Each installer writes the agent's own files:

- Claude Code writes `enabledPlugins` in `~/.claude/settings.json` and its plugin store (Documented).
- Codex writes a `plugins` table in `config.toml` and a cache folder (Documented in source).
- OpenCode writes the `plugin` list in `opencode.json` (Documented in source).
- Pi writes `packages` in `~/.pi/agent/settings.json` (Documented).
- Gemini CLI writes a folder in `~/.gemini/extensions`, with an install record inside (Documented in source).

The Codex and OpenCode installers write the same file that holds Fleet's owned entries.
Fleet runs each installer first. It then reads the file again before it writes an entry.
A Git or web source needs the network. An installed plugin loads at the next start. Both facts are Documented for Claude Code.

### Claude Code

Tested version: 2.1.326.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.claude/skills/NAME/SKILL.md`. An entry can be a link. | One link per skill, into `~/.agents/skills`. | Documented |
| Rule sets | `~/.claude/CLAUDE.md`, with include lines. | One include line that names one file in the tree. | Documented |
| MCP servers | `mcpServers` in `~/.claude.json`, or `.mcp.json` inside a plugin folder. | `.mcp.json` inside the tree's plugin folder. | Tested |
| Hooks | `hooks` in `~/.claude/settings.json`, or `hooks/hooks.json` inside a plugin folder. | `hooks/hooks.json` inside the same folder. | Tested |
| Connectors | Account-level. No local file declares them. | With a `url`: an HTTP entry in `.mcp.json`, plus a reported sign-in. Otherwise a reported step. | Documented |
| Plugins | `enabledPlugins` and `extraKnownMarketplaces` in `~/.claude/settings.json`, plus a plugin store. | Fleet runs the installer. | Tested for the keys and the help text |

The tree holds one generated plugin folder. One link leads to it: `~/.claude/skills/fleet`.
The folder holds `.claude-plugin/plugin.json`, `.mcp.json`, and `hooks/hooks.json`.
Claude Code lists the linked folder as loaded, under the name `fleet@skills-dir` (Tested).
Its own help text says that such a folder loads at the next session (Documented). Fleet edits no shared file for it.
Claude Code skips its trust check for this location (Documented).
Servers appear as `plugin:fleet:NAME` (Tested). Tools appear as `mcp__plugin_fleet_NAME__TOOL` (Documented).
A permission rule that names a tool must use that longer name.
An administrator policy can block folders of this kind (Documented). Fleet then reports the block.
Ruling 4 chose this folder over owned entries in `~/.claude.json` and `~/.claude/settings.json`.
Claude Code writes its own state into `~/.claude.json` (Tested). Community reports say it reverts outside edits (Unverified).

Claude Code documents no shared skills folder, so each skill needs a link.
A skill link can point to a folder elsewhere on disk (Documented).
Claude Code loads a skill once when several locations point at one target (Documented).
The plugin folder could carry every skill through one link. `claude plugin details` then lists the skills (Tested).
The validator warns that a linked folder inside a plugin loads only when it points inside certain folders (Tested).
No session test confirms that route. A plugin skill also gains a prefix: `/fleet:NAME` (Documented).
Section 11, item 5 covers this choice.

An include line starts with `@` and names a path. Absolute paths and home paths work (Documented).
User-level includes load without an approval dialog (Documented).
Includes nest four levels deep. Claude Code skips an included file over 4 MiB (Documented).
A plugin cannot carry rules. Claude Code does not load a `CLAUDE.md` at a plugin's root (Documented).
Claude Code also reads rule files in `~/.claude/rules` (Documented). Section 11, item 7 covers that alternative.

Some plugins install through a command that their plugin marketplace declares.
Claude Code shows that command and asks for confirmation (Tested for the help text).
Without a terminal, the install needs `--yes`, or the reviewed command's hash through `--accept-command` (Tested for the help text).
This design proposes that Fleet never passes `--yes`. Fleet reports such a plugin as a manual step (section 11, item 8).
Claude Code downloads an enabled plugin by itself after start (Documented).

### Codex

Tested version: 0.169.0.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.agents/skills`. The older `~/.codex/skills` still loads. | None. Codex reads the shared folder. | Documented |
| Rule sets | `~/.codex/AGENTS.md`. No include syntax exists. | One owned key in `config.toml`: `developer_instructions`. | Documented in source |
| MCP servers | `mcp_servers` tables in `~/.codex/config.toml`. | Owned entries in that file, until phase 3. | Tested |
| Hooks | `~/.codex/hooks.json`, or a `hooks` table in `config.toml`. | One entry per hook in `~/.codex/hooks.json`, plus a reported trust step. | Documented |
| Connectors | Apps are account-level. Local settings only switch them on or off. | With a `url`: an owned HTTP entry, plus `codex mcp login NAME`. Otherwise a reported step. | Documented |
| Plugins | `plugins` tables in `config.toml`, plus an installed copy under `~/.codex/plugins/cache`. | Fleet runs the installer. | Tested for the keys and the help text |

Codex offers no include file for settings and no extra settings file that always loads (Documented).
A named profile file needs a flag on every run (Documented), so it cannot carry Fleet's entries.
Codex writes `config.toml` itself. The test machine's file held plugin switches and hook trust records (Tested).
Choosing "always allow" for a tool adds a key inside the server's table (Documented in source).
Codex starts a `stdio` server with a fixed variable list, plus the names in `env_vars` (Documented in source).
A plugin entry without an installed copy is an error (Documented in source). Settings alone cannot enable a plugin.
Codex follows a linked skill folder but ignores a linked `SKILL.md` file (Documented). Fleet links folders only.

Codex reads `AGENTS.override.md` or `AGENTS.md` and stores the text unparsed (Documented in source). No include line works.
`developer_instructions` adds text to every session (Documented in source). A rule change rewrites that key.
A session-start hook can also add text to a session (Documented). Section 11, item 7 covers that alternative.

Each hook entry names a script in the tree. Section 11, item 6 covers the choice of one entry per hook.
Codex skips a new or changed hook until the user trusts it in `/hooks` (Documented). Fleet reports that step and never bypasses it.
Codex stores one trust record per hook. Each record holds a key and a hash.
The hash covers the entry: its event, matcher, command text, and timeout (Documented in source).
The hash does not cover the script's content. A changed script in the tree therefore needs no new trust step (Documented in source).
The key includes the file path and the entry's position (Documented in source). Fleet appends its hooks and keeps their order.
Removing one entry moves the entries after it. Codex then asks the user to trust those entries again.
Codex warns at start when both hook locations hold hooks (Documented). It still loads both (Documented in source).
Fleet reports that case before it writes.
Codex writes `hooks.json` only when it imports another agent's hooks into a missing or empty file (Documented in source).
A linked `hooks.json` also loads (Documented in source). No test covers it.

### Gemini CLI

Tested version: 0.50.0. The newest stable version is 0.68.0 (Documented).

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.gemini/skills` and `~/.agents/skills`. | None. Gemini CLI reads the shared folder. | Tested |
| Rule sets | `~/.gemini/GEMINI.md`, or a context file in an extension folder. | One context file inside the tree's extension folder. | Tested for the listing |
| MCP servers | `mcpServers` in `~/.gemini/settings.json`, or in an extension folder. | `gemini-extension.json` inside the tree's extension folder. | Tested |
| Hooks | `hooks` in `settings.json`, or `hooks/hooks.json` in an extension folder. | `hooks/hooks.json` inside the same folder. | Documented |
| Connectors | An HTTP server entry, then a sign-in inside Gemini CLI. | An HTTP entry in the extension file, plus a reported sign-in. | Unverified for the sign-in |
| Plugins | Extensions: `gemini extensions install SOURCE`, which asks for consent. | Fleet runs the installer. | Tested for the command |

An extension is Gemini CLI's plugin type. The tree holds one generated extension folder.
One link leads to it: `~/.gemini/extensions/fleet`.
Gemini CLI loads a hand-placed folder without install records (Tested). A link to the folder also loads (Tested).
Gemini CLI lists each server as "from fleet" (Tested).
`gemini extensions list` shows the folder's context file and servers through the link (Tested).
An extension's `GEMINI.md` loads in every session where the extension is active (Documented).
An include line in `~/.gemini/GEMINI.md` cannot reach the tree.
Gemini CLI refuses an include target outside the including file's folder (Documented in source).
A Git repository that encloses the file widens that limit to the repository (Documented in source).
Version 0.68.0 resolves links before that check (Documented in source).
An allow-list setting, `security.allowedExtensions`, blocks a folder without install records (Documented in source). Fleet then reports the block.
`$NAME` and `${NAME}` resolve in settings and extension files (Tested for `args`; Documented for `env` and `headers`).
Gemini CLI disables user-level and extension servers in a folder that the user has not trusted (Tested).
Gemini CLI writes `settings.json` itself. The test machine's file held sign-in and editor keys (Tested).
A newer version replaces a linked `settings.json` with a regular file (Documented in source).
Hook timeouts count milliseconds (Documented). Claude Code and Codex count seconds, so the agent catalog must convert.
Gemini CLI 0.50.0's own migrate command copies seconds as milliseconds (Tested). Version 0.68.0 converts them (Documented in source).
`--consent` accepts the install's risk warning (Tested for the help text).
`--skip-settings` skips the settings questions (Tested for the help text).
This design proposes that Fleet passes both options and reports a local path source as a manual step (section 11, item 8).
Uninstall asks nothing (Documented in source).

### Antigravity

Google replaced Gemini CLI with Antigravity for unpaid and Google One users on 2026-06-18 (Documented).
Gemini CLI still works with paid API keys (Documented). Fleet 0.5.0's agent catalog has no Antigravity entry.
Ruling 12 adds the entry as detect-only. Fleet detects Antigravity and renders nothing for it.
No test ran. Antigravity is not installed on the test machine.
The table shows what Fleet would write after a tested install confirms the paths.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.gemini/config/skills`. The terminal command also reads `~/.gemini/skills`. | One link per skill in `~/.gemini/config/skills`. | Documented |
| Rule sets | `~/.gemini/config/AGENTS.md` and three other files, with include lines. | One include line. | Documented |
| MCP servers | `~/.gemini/config/mcp_config.json`. | Owned entries in that file. | Documented |
| Hooks | `~/.gemini/config/hooks.json`. | Owned entries in that file. | Documented |
| Connectors | A remote server entry. Antigravity then handles the sign-in. | An owned entry, plus a reported sign-in. | Documented |
| Plugins | A plugin folder in `~/.gemini/config/plugins`. | Fleet runs the installer. | Documented |

The terminal command is `agy` (Documented). A skill bundled in Gemini CLI 0.50.0 names the same command (Tested).
Antigravity's documentation never names `~/.agents/skills`. Antigravity therefore gets its own skills folder.
A remote server uses the key `serverUrl`; `url` also works (Documented).
The documentation names no variable reference form for `mcp_config.json`. One community report says references fail.
Until a reference test passes, Fleet skips an Antigravity server that needs a secret.
`agy plugin import gemini` converts Gemini CLI extensions (Documented).
Whether a generated plugin folder can carry Fleet's servers and hooks is Unverified.
An Antigravity include line puts a label in square brackets and a path in parentheses, after `@` (Documented).
A home path works there (Documented).
A plain `@path` line includes nothing in Antigravity (Documented). Gemini CLI uses that plain form.
Both agents read `~/.gemini/GEMINI.md` (Documented). Fleet therefore writes no include line into that file.
Antigravity limits a rule file to 24,000 bytes after includes (Documented).
A marketplace plugin install needs an `agy` sign-in (Documented). Its prompts are Unverified.

### OpenCode

Tested version: 1.17.8. A second release line, version 2, renames several keys (Documented).

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.config/opencode/skills`, `~/.agents/skills`, and `~/.claude/skills`. | None. OpenCode reads the shared folder. | Tested |
| Rule sets | `~/.config/opencode/AGENTS.md`, plus files that the `instructions` key lists. | One owned `instructions` entry that names one file in the tree. | Documented |
| MCP servers | `mcp` entries in the global settings files. | Owned entries in `~/.config/opencode/opencode.json`, until phase 3. | Tested |
| Hooks | None as commands. Plugins are JavaScript or TypeScript code. | Not rendered. Reported as unsupported (ruling 10). | Documented |
| Connectors | A remote server entry, then `opencode mcp auth NAME`. | An owned entry, plus a reported sign-in. | Tested for the command |
| Plugins | Names in the `plugin` list, or code files in `~/.config/opencode/plugins`. | Fleet runs the installer. No removal command exists. | Tested for the command |

OpenCode merges three global files: `config.json`, then `opencode.json`, then `opencode.jsonc`. The last file wins (Tested).
The test machine keeps hand-written settings in `opencode.jsonc`, with comments. Fleet 0.5.0's agent catalog names only `opencode.json`.
Fleet writes `opencode.json` and never edits `opencode.jsonc`. A same-name entry in `opencode.jsonc` wins inside OpenCode.
Fleet then skips the item for OpenCode and reports the shadowing entry once. The run continues.
OpenCode's own commands also write `opencode.json` first (Documented in source). The file therefore holds owned entries.
OpenCode rewrites a settings file that lacks `$schema` (Documented in source). Fleet must write that key.
OpenCode stops with an error on an invalid entry (Tested with `opencode debug config`). Fleet must validate each entry before it writes.
Version 2 drops `config.json` and still reads the version 1 shape (Documented). Fleet writes the version 1 shape.
A local entry holds `type`, a `command` list, and `environment`. A remote entry holds `type`, `url`, and `headers` (Tested).
`{env:NAME}` resolves in both (Tested). An unset variable becomes an empty string (Documented).
OpenCode expands no include line in an instruction file (Documented).
An `instructions` entry accepts a home path, an absolute path, or a file pattern (Documented).
OpenCode's resolved configuration keeps an entry with a home path (Tested).
OpenCode reads `~/.claude/CLAUDE.md` when it has no `AGENTS.md` of its own (Documented).
OpenCode 1.17.8 has no command that removes a plugin (Tested for the help text). `opencode uninstall` removes OpenCode itself.
Section 11, item 10 covers the removal of an OpenCode plugin.
OpenCode installs every module in the `plugin` list at start (Documented). Version 2 adds a remove command (Documented).

### Pi

Tested version: 1.3.0. Mise labels this install 0.80.6; the binary reports 1.3.0.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.pi/agent/skills` and `~/.agents/skills`. | None. Pi reads the shared folder. | Documented |
| Rule sets | `~/.pi/agent/AGENTS.md`. No include syntax exists. | One link at `~/.pi/agent/APPEND_SYSTEM.md`, into the tree. | Documented for the file; Unverified for a link |
| MCP servers | `mcpServers` in `~/.pi/agent/mcp.json`. | Owned entries in that file, until phase 3. | Tested |
| Hooks | None as commands. Extensions are TypeScript files in `~/.pi/agent/extensions`. | Not rendered. Reported as unsupported (ruling 10). | Documented |
| Connectors | An HTTP server entry, then `pi mcp login NAME`. | An owned entry, plus a reported sign-in. | Tested for the command |
| Plugins | Pi packages: `packages` in `~/.pi/agent/settings.json`. | Fleet runs the installer. | Tested for the help text |

Pi writes to `mcp.json` itself. It saves enable and exposure changes into the entry (Documented).
Pi accepts a hand-written entry with a variable reference and a disabled flag (Tested).
Pi's newer documentation says it installs a missing package at the next start (Documented).
Pi reads the first instruction file that it finds and stores the text unparsed (Documented in source). No include line works.
`APPEND_SYSTEM.md` adds its text to Pi's system prompt (Documented).
A trusted project's own `APPEND_SYSTEM.md` takes precedence over the user-level file (Documented). Pi then skips Fleet's rules there.
Section 11, item 7 covers this pointer.

### Grok

No test ran. Grok Build is not installed on the test machine. Every claim below is Documented at best.
Ruling 12 keeps Grok's catalog entry as detect-only. Fleet detects Grok and renders nothing for it.
The table shows what Fleet would write after a tested install confirms the paths.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.grok/skills`, `~/.agents/skills`, and `~/.claude/skills`. | None. Grok reads the shared folder. | Documented |
| Rule sets | Instruction files in `~/.grok`, plus `~/.grok/rules`. No include syntax exists. | One link in `~/.grok/rules`. | Documented in source |
| MCP servers | `mcp_servers` tables in `~/.grok/config.toml`. | Owned entries in that file. | Documented |
| Hooks | One JSON file per hook set in `~/.grok/hooks`. JSON is a structured file format. | One owned file: `~/.grok/hooks/fleet.json`. | Documented |
| Connectors | The connectors command is an alias of the MCP command. | An owned HTTP entry, plus a reported sign-in. | Documented |
| Plugins | `plugins` and `marketplace.sources` tables in `config.toml`. | Fleet runs the installer. | Documented |

Grok also loads Claude Code's servers, hooks, skills, and plugins (Documented).
It reads `~/.claude/CLAUDE.md` and `~/.claude/rules` by default (Documented in source).
Fleet keeps Claude Code's servers and hooks inside the generated plugin folder. Whether Grok loads that folder is Unverified.
A community tool also installs a `grok` command and uses `~/.grok` (Documented in its source).
That tool keeps `user-settings.json`, not `config.toml`. Detection must tell the two apart.

### Hook events

| Neutral event | Claude Code | Codex | Gemini CLI | Antigravity | Grok |
| --- | --- | --- | --- | --- | --- |
| `session-start` | `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` |
| `session-end` | `SessionEnd` | `SessionEnd` | `SessionEnd` | None | `SessionEnd` |
| `prompt-submit` | `UserPromptSubmit` | `UserPromptSubmit` | `BeforeAgent` | Unverified | `UserPromptSubmit` |
| `before-tool` | `PreToolUse` | `PreToolUse` | `BeforeTool` | `PreToolUse` | `PreToolUse` |
| `after-tool` | `PostToolUse` | `PostToolUse` | `AfterTool` | `PostToolUse` | `PostToolUse` |
| `stop` | `Stop` | `Stop` | `AfterAgent` | `Stop` | `Stop` |
| Evidence | Documented | Documented | Tested | Documented | Documented |

These five agents share one entry shape: an event, an optional `matcher`, and a list of commands.
Gemini CLI's column comes from its own migrate command (Tested).
Antigravity also documents `PreInvocation` and `PostInvocation`. Their match for `prompt-submit` is Unverified.
Timeout units differ. Gemini CLI counts milliseconds. Claude Code, Codex, and Antigravity count seconds (Documented).
Grok's timeout unit is Unverified.
Tool names differ per agent. Gemini's migrate command maps `Bash` to `run_shell_command` (Tested).
Other tool class names are Unverified.
Whether each agent runs a hook command through a shell is Unverified.
OpenCode and Pi run hooks only through code (Documented). The first build reports their hooks as unsupported (ruling 10).
Bridge plugins are the named future fix. Appendix A describes that study.

### Findings for the agent catalog

The tests found six gaps in Fleet 0.5.0's agent catalog. They are facts for the builder. Ruling 12 settles the fifth.

1. Pi's package moved to `@earendil-works/pi-coding-agent` at version 0.74.0 (Documented). The catalog's Mise names lack it.
2. OpenCode users can keep settings in `opencode.jsonc` (Tested). The catalog names only `opencode.json`.
3. With Codex and Gemini CLI both on, the catalog's skill folders make Gemini CLI warn at start (Tested).
4. A community tool shares Grok's command name and its `~/.grok` folder (Documented). Detection cannot tell them apart today.
5. Antigravity has no entry, although Google now directs most Gemini CLI users to it (Documented).
6. The Codex and Pi documentation links now redirect to new addresses (Tested for Codex; Documented for Pi).

## 3. Foundation model and where the store lives

### Three layers

The foundation model sorts every file by its owner. The foundation is the tracked base that Fleet ships.

| Layer | Holds | What changes it |
| --- | --- | --- |
| Foundation | The tracked content that Fleet installs, plus the manifest. | An update, from the content repository. |
| Library | Components that a machine pulls in from a marketplace. | A marketplace refresh. |
| The user's own files | Every file that Fleet neither installs nor records. | The user and the agents. |

Seven rules follow:

1. A content update and its diff apply only to the foundation.
2. The only conflict surface is a hand edit inside the foundation, on a file that an update also changes.
3. A pulled-in component lives in the library. Its installation record lives in the manager store.
4. No pulled-in file enters the user's tracked content.
5. An update never rewrites the user's own files.
6. A refresh replaces a pulled-in component. Fleet saves any edited copy in the run backup and prints one line.
7. Unmanaged content is invisible to an update. Fleet shows no prompt, no refusal, and no reminder line.

Content is unmanaged when Fleet neither installs it nor records it.

### One changed behavior

The model changes one current behavior.
Today, check and update refuse a content checkout with any uncommitted change. An untracked file counts.
Under the model, an uncommitted unmanaged file in the content checkout cannot block setup or an update.
An uncommitted edit to a managed file still blocks.
In the content checkout, a managed file is a tracked file that Fleet installs, or the manifest.

### The layers in this module

| Layer | Agent configuration in it |
| --- | --- |
| Foundation | The `agents` table, skill folders, rule sets, and hook scripts in the content repository. |
| Library | Packages that the machine took from a marketplace (section 7). |
| The user's own files | Each agent's settings files, sign-in stores, hand-written entries, and hand-made skills. |

The tree is not a fourth layer. Fleet builds it from the foundation and the library, and can rebuild it.

### Three placements

The first draft compared three places for the store:

| Placement | Meaning | Result |
| --- | --- | --- |
| Content repository only | Declarations and machine choices both live in content. | Breaks rule 1. Machine choices would enter content. |
| Manager store only | Fleet keeps declarations in the manager store. | Breaks rule 1. Content no longer declares. Machines need a new transfer path. |
| Split | Each kind of data lives with its owner. | Meets all three rules. |

The ruled design keeps the split by owner. The foundation model adds the library and its record.

### Where each kind of data lives

| Data | Lives in | Reason |
| --- | --- | --- |
| Declarations, skills, rule sets, and hook scripts | Content repository | Content declares. Git carries history and transfer. Nova can read the same files. |
| Agent catalog | Manager repository | Native paths and formats change with each agent. The catalog and renderer release together. |
| Machine choices and exceptions | Profile | Rule 1. Choices differ per machine. |
| Pulled-in components | Library | The foundation model. Git does not track them. |
| Marketplace installation record | Manager store | The foundation model. It names each source, version, pin, and package. |
| The tree | A Fleet-owned folder on the machine | The deployment model. Fleet can rebuild it. |
| Inventory | Manager store: `~/.fleet/agents.json` | Rule 2. Fleet lists every output. |
| Pointers and owned entries | Each agent's native location | Fleet can rebuild them from content, agent catalog, and profile. |
| Installed plugins | Each agent's own plugin store | The agent's installer writes them. The inventory names each one. |
| Agent sign-in sessions and tokens | Each agent's own store | Fleet never reads or writes them. |
| Secret values that servers need | `~/.secrets`, as today | The manager already writes this file from secret references. This module adds only variable names. |

The reasoning applies each settled rule:

1. Rule 1 puts declarations in content and choices in the profile. The split follows that line exactly.
2. Rule 2 needs one list of outputs. The inventory already exists, so it stays in manager state.
3. Rule 3 needs files that Nova can read without Fleet code. Plain TOML, Markdown, and skill folders in Git meet that need.
4. Git already transfers content between machines. A manager-side store would need a sync engine, which the manager forbids.
5. The agent catalog stays out of content. A content repository then never names a native path, so it stays neutral.
6. A marketplace choice belongs to one machine. Its record therefore stays in the manager store, never in content.

The tree, pointers, and owned entries are never a source of truth. Delete any of them, run `fleet update`, and Fleet rebuilds it.
This design leaves the library's folder to the builder. It must sit outside the user's tracked content.

### Inventory records

The inventory record for each output holds its path, kind, agent, source item, and fingerprint.
An owned entry adds the entry's name and the rendered keys and values. Rendered values hold no secrets.
A generated folder lists each file inside it.
A plugin record holds the agent, the plugin `id`, and the plugin marketplace.
The inventory keeps owned entries apart from owned paths. A shared file is never an owned path.

## 4. Precedence, drift, and repair

### What Fleet owns

Fleet owns four kinds of output. The inventory lists each one:

1. The tree and every file in it.
2. Each pointer.
3. Each owned entry, where an agent accepts no pointer.
4. Each plugin that Fleet installed.

Everything else in an agent's folders belongs to the user or the agent. An update never rewrites it.
For an owned entry, Fleet owns the keys that it wrote. Other keys in the same file stay untouched.
In a table of named entries, the name identifies Fleet's entry. In a list of hooks, the rendered command identifies it.

### Where a hand edit lands

The foundation model rules the first two rows and the last row. This design proposes the two rows between them:

| Place of the edit | Rule |
| --- | --- |
| A managed content file, such as an installed skill | The existing file conflict path applies. Skills are ordinary content. |
| A pulled-in component | A refresh replaces it. Fleet saves the edited copy in the run backup and prints one line. |
| A file in the tree | Proposed: the same rule as for a pulled-in component (section 11, item 2). |
| A pointer or an owned entry | Proposed: the edited value becomes the user's own. Fleet keeps it and reports once (section 11, item 1). |
| Anything that Fleet did not write | The user's own. Fleet never rewrites it. |

### Migration from today's generator

The new renderer replaces today's generator. One code path remains.

1. Stop writing minimal settings files. Each recorded settings file becomes a shared file.
2. Drop the whole-file record for each settings file. Keep the file when it changed or when an entry needs it.
3. Remove a settings file only when it still matches its record and no entry needs it.
4. List each kept file once in the run account as released to the user.
5. Create no new skill link from `agents/skills`. Content moves its skills to `.agents/skills`.
6. Treat each recorded skill link as stale once content installs that skill at `~/.agents/skills`.
7. Remove a stale link at `~/.agents/skills/NAME` before chezmoi installs the real folder there.

This design proposes one rule for a repository that has not moved its skills yet.
The recorded links stay in place, and Fleet prints one line that names the move.

### First contact and later hand edits

Fleet compares each planned pointer and owned entry with the home folder before any write:

| Finding | Action |
| --- | --- |
| Nothing exists at the target | Create the output and record it. |
| An unrecorded value exists at the target | The value is the user's own. Keep it, skip what Fleet planned there, and report the skip once. |
| A recorded output still matches its record | Update it when the declaration changed. Otherwise leave it. |
| A recorded output differs from its record | The value changed outside Fleet. Keep it, release the record, and report the change once. |
| A recorded output is missing | Create it again. A missing output is drift that needs no decision. |

The second and fourth rows are this design's reading of the foundation model. Section 11, item 1 asks the owner to confirm it.
An unrecorded value that equals the rendered value is still the user's own. Fleet does not take it over.
Fleet checks every file that the agent merges. A same-name value in any of them counts as the user's own.
Each report names the value that holds the place, and its file.
These rules never prompt and never stop a run. Fleet reports each case in one run, not in every run.
A released value is then the user's own. Fleet skips that item for that agent while the value stays.
The first draft asked in a terminal instead. Appendix C keeps that rule as the alternative.

### Unattended runs

A run without a terminal follows the same rules. This covers scripted, terminal-interface, and remote updates.
A kept value, a skipped file, and a missing output never stop a run. Fleet recreates a missing output.
Fleet never chooses overwrite for the user.
A changed skill file still follows the existing file rules. An unattended file conflict stops without an overwrite.

### Detection between runs

Agent drift means a missing output, or a changed output that no update has released yet.
Agent drift joins the existing drift signal:

- `fleet status` counts agent drift in its Drift line.
- `fleet diff` prints one patch per changed item and exits 1.
- `fleet doctor` fails its managed-files check and names the fix.
- The update gate treats agent drift as a `drift repair` trigger. A missing output counts.

Status and doctor read local files only. Diff and the update gate fetch first, as they do today.
None of these commands runs an agent's command.
A patch can show secret values that a user wrote by hand. The existing diff warning applies.

### Repair choices

1. Take the rendered value back: remove the hand-written value, then run `fleet update`.
2. Keep the local value on this machine: add a `local` exception to record that choice.
3. Go without the item on this machine: add an `off` exception for the item.
4. Change the declaration: edit the manifest, commit, push, and run `fleet update`.
5. Stop managing the item everywhere: remove its declaration.

### Removal

Ruling 6 names two cases of a stale output: its declaration disappears, or a domain change excludes it.
This design adds a third case: an `off` exception.
Today the generator keeps every stale output. It prints one progress line, and the run account omits it.
Ruling 6 sets three rules:

1. Fleet deletes a stale output that still matches its record. The backup keeps a copy.
2. A changed stale output stays. Fleet prints one report line for it.
3. Turning an agent off keeps that agent's outputs, as it does today.

The run account lists every removed output and every kept output.
For a changed stale output, this design proposes that the record stays and marks the output as kept.
The line then does not repeat, and no output loses its record.
Fleet removes only listed outputs. It never deletes an agent folder recursively.
A pointer can outlive its items. An empty folder in the tree does no harm.

### Plugins

Plugins are fully managed (ruling 7). Fleet installs and uninstalls through each agent's own installer.
The inventory drives both actions:

1. Install a declared plugin that the inventory does not list.
2. Uninstall a listed plugin that no declaration selects for this machine.
3. Never uninstall a plugin that the inventory does not list. That plugin is the user's own.

This design proposes four more steps around those actions:

1. List each planned install and uninstall in the review, as a software change.
2. Run an installer after the backup, with options that need no answer.
3. Record a plugin only after its installer succeeds. A failed install ends the run as incomplete and names the command.
4. Run each installer before the entry writes for that agent's files. Fleet then reads those files again.

This design also proposes one check at each update: does the agent hold each declared plugin?
Fleet reads the plugin records that the agent's installer writes. Section 2 lists them. Fleet runs no agent command for the check.
A plugin that the agent holds and the inventory does not list is the user's own (section 11, item 9).
A listed plugin that the agent no longer holds is drift. Fleet installs it again.

Three limits follow from the evidence:

- An installer writes files that Fleet does not list. Fleet records the plugin, not those files.
- Fleet cannot back up what an installer writes. This design proposes that rollback keeps installed plugins and lists them.
- An agent can install a plugin by itself at start. The check then finds that plugin present.

Fleet never runs an agent's command to write settings (ruling 8). A plugin install is the only exception.
Section 11, item 8 lists the commands that an install includes.

### Uninstall and rollback

Today uninstall deletes every path that the inventory lists. Rollback rewrites each saved file whole.
Neither action may treat a shared file as an owned path:

1. On uninstall, remove each pointer, then the tree, then each owned entry. Never delete a shared file.
2. Remove the whole entry, including keys that the agent added. The backup taken first keeps a copy.
3. On rollback, restore owned paths as today.
4. On rollback, restore only Fleet's entries in a shared file, from the backup copy.
5. Keep every other value that the agent or user wrote after that backup.

Rule 2 removes agent-added keys because an entry without Fleet's keys is not valid.
A released value is not in the inventory, so uninstall leaves it.
Uninstall keeps installed tools today. Section 11, item 11 asks whether it also keeps installed plugins.

### Shared files

A pointer line and an owned entry both live in a file that Fleet does not own. Six rules protect that file:

1. Parse the file first. Skip the file when it is invalid, holds comments that a rewrite would lose, or is a link that leaves home.
2. Change only the owned entry. Keep key order.
3. Parse the result. Write nothing unless every other value equals its earlier value.
4. Write atomically and keep the file's permissions. An atomic write replaces the file in one step.
5. Read the file again just before the write. Skip the write when the file changed since the plan.
6. Skip a file that content also manages. Two sources cannot own one output.

The first draft stopped the run in these cases. This design proposes a skip, because the file is the user's own.
Fleet reports a skipped file once and renders none of its entries. The run continues.
An agent can add keys inside an owned entry, such as a disabled flag or a tool approval. Those keys belong to the user.
Fleet compares only the keys that it wrote (ruling 5). An agent-added key never counts as drift.
An include line is an owned entry of one line. This design proposes that Fleet identifies it by its exact text.
When content manages the instruction file, rule 6 applies. Fleet then reports the line for the author to add.
A backup of a shared file copies every value in it. Backups stay private, as they do today.

### Precedence inside an agent

Each agent merges its own layers. A project setting can override a user-level setting.
Fleet writes user-level outputs only. A project override is the agent's normal behavior, not drift.

## 5. Nova consumption

Nova would need six things from this format:

| Need | Does the design give it? |
| --- | --- |
| Files that it can read without Fleet code | Yes. The store is plain TOML, Markdown rule sets, and skill folders in Git. |
| A stable name for every item | Yes. Each item has a unique key, such as `mcp.docs-search`. |
| A scope label for every item | Yes. `domains` carries it. Nova must map domains to its own scopes. |
| No secret values | Yes. The store holds variable names and references only. |
| A version to cite | Yes. `agents.schema` and the content commit identify what Nova read. |
| A render root other than home | Partly. The format never names home. Nova must supply its own renderer or catalog. |

Nova needs neither the profile nor the inventory. Both describe one machine's Fleet state.
The partial answer is deliberate. The agent catalog lists paths under home, and Nova renders elsewhere.
Nova can read the agent catalog as a reference. Fleet gains no dependency either way.
The later additions keep that property. A marketplace is a Git repository with a plain catalog file.
The gateway speaks MCP, an open protocol. Any MCP client can connect to it, and Fleet depends on none.

## 6. Questions for the owner, with rulings

The owner ruled on every question. Each entry keeps its recommendation as the record of the advice given.
A line that starts with RULED states the ruling.

1. Is the agent module manager code, or the first add-on under the add-on contract?
   Recommendation: manager code. The generator, wizard step, profile field, and inventory already live in the manager.
   No add-on host exists yet. The module can still keep the contract's removal protections as rules.
   RULED: The module is manager code, not an add-on.
2. Where does the `agents` table live: inside `fleet.toml`, or in a separate file that the manifest names?
   Recommendation: inside `fleet.toml`. One manifest keeps one validator and one schema. A later split stays possible.
   RULED: The `agents` table lives inside `fleet.toml`.
3. Where should skills and hook scripts live, and where should links point?
   Today, skills live in `agents/skills` and links point into the content checkout.
   The manifest's `paths` key also installs an unused copy at `~/agents/skills`.
   Option A keeps checkout links and drops the unused copy.
   Option B stores these files under real home names, such as `.agents/skills`. chezmoi then installs them as ordinary content.
   Option C copies them into a manager-owned folder and links there.
   Recommendation: option B. Existing code then lists, backs up, and repairs these files. Five agents read the shared folder without links.
   Option B has two costs. An edit reaches agents only after commit and update. A skill's domain limit needs new converter code.
   Option A lets agents read uncommitted edits.
   RULED: Skills are ordinary content, installed at `~/.agents/skills`.
4. Should Fleet prefer an agent's self-contained folder over entries in shared files, where the agent offers one?
   Recommendation: yes. Use a generated plugin folder for Claude Code and a generated extension folder for Gemini CLI.
   Accept Claude Code's longer server and tool names.
   RULED: Fleet prefers an agent's self-contained folder over entries in shared files.
5. For an owned entry, should Fleet compare only the keys that it wrote, or the whole entry?
   Recommendation: only the keys that it wrote. Codex and Pi add keys inside entries, and a whole-entry check would report false drift.
   RULED: Fleet compares only the keys that it wrote. An agent-added key never counts as drift.
6. Should Fleet remove stale outputs that still match their record?
   Recommendation: yes for a removed declaration or a domain change. Keep today's rule when the user turns an agent off: keep the outputs.
   RULED: Fleet deletes a stale output that still matches its record (declaration removed, domain changed). A changed output stays, with one report line. Turning an agent off keeps its outputs.
7. How far should Fleet go with plugins: report only, write an agent's enable setting, or run an agent's installer?
   Recommendation: report only in the first build. This matches tool pins: content defaults never install software.
   Add an explicit per-machine opt-in later.
   RULED, against the recommendation: Plugins are fully managed. Fleet installs and uninstalls them through the agent's own installer, driven by the record.
8. Today the generator starts no agent. Should this module keep that rule, even where an agent's own command could write settings?
   One such command is `codex mcp add`.
   Recommendation: keep the rule. A file edit shows in the run account before and after. A command's side effects do not.
   Even a help command wrote files into two agents' folders during the tests.
   RULED: Fleet never runs an agent's command to write settings. A plugin install under ruling 7 is the only exception.
9. How does one machine turn one item off, or keep its own value?
   Option A: a group in the manifest lists agent items, and the machine turns that group off.
   Option B: the profile keeps a list of items that are off on this machine, and a command edits it.
   Recommendation: option B. It needs no content change, so it can settle a conflict at once. The conflict prompt can then offer it.
   RULED: Exceptions live in the profile: an item off, or an item with a local value. A command edits the list, and updates honor it.
10. Should Fleet generate code so that OpenCode and Pi can run declared hooks?
    Recommendation: no in the first build. Report those hooks as unsupported. Generated code in two languages needs its own review.
    RULED: No generated hook code in phase 1. Fleet reports those hooks as unsupported. Bridge plugins are the named future fix, with a design study in phase 4.
11. Should Fleet offer a read-only import that prints manifest entries from an agent's existing settings?
    Recommendation: yes, after the first build. It is the shortest path from one configured agent to a shared store.
    RULED: Yes. A read-only import prints `agents` table entries from an agent's existing settings. It comes in phase 4.
12. Should Antigravity get a catalog entry now, and should Grok stay marked as supported?
    Recommendation: add Antigravity. Detect both agents, but render for neither until one tested install confirms the documented paths.
    RULED: Antigravity enters the catalog now, and Grok keeps its entry. Fleet detects both and renders for neither until a tested install confirms the paths.
13. Should this module also carry shared instruction files, such as one `AGENTS.md` for every agent?
    Recommendation: not in this module. Plain content can already install those files under their real names.
    RULED: Whole instruction files stay out, and plain content installs them. Declared rule sets are items.

### Additions ruled during review

The owner also ruled these additions. No question in the first draft asked for them.

1. The deployment model is the primary shape (section 2).
2. The foundation model sets three layers of ownership (section 3).
3. Content defines domains. Core always applies, and each machine carries one domain (section 9).
4. Remote profile management and remote domain assignment run through dispatch (section 9).
5. Marketplaces arrive in phase 2 (section 7). Their name is open.
6. The gateway arrives in phase 3 (section 8).
7. Provider entries are out (section 1).
8. Bridge plugins for OpenCode and Pi get a design study in phase 4. Their delivery method is open.

## 7. Marketplaces

Phase 2 builds marketplaces. This section records the ruled design and marks each proposal.

### What a marketplace is

A marketplace is a Git repository with a catalog file. The catalog file lists the repository's packages.
A package bundles any mix of items: skills, rule sets, MCP servers, connectors, plugins, and hooks.
The project's own marketplace is one such repository. Fleet consumes it through the same mechanism.
It gets no special internal path.
The name is open and does not block the build. The owner picks it before phase 2 ships.
Claude Code and Codex use the word marketplace for their own plugin catalogs. This document calls those plugin marketplaces.

### Layers

A package that a machine takes is a pulled-in component (section 3):

1. Fleet places the package in the library. Git does not track the library.
2. Fleet records the installation in the manager store.
3. No file enters the user's tracked content.

This design draws one consequence: a marketplace choice belongs to one machine.
It does not travel with the content repository.

### Commands

Fleet manages marketplaces through four actions:

| Action | Effect |
| --- | --- |
| Add | Register a marketplace by its Git address. |
| Update | Fetch a marketplace and refresh its installed packages. |
| Delete | Remove a marketplace and its record. |
| List | Show each marketplace's source, version, pin, and installed packages. |

The rulings name the list command: `fleet marketplaces list`. The builder names the other three.
This design proposes that a delete also removes that marketplace's installed packages.
Their outputs then become stale. Section 4 sets the removal rules.

### Selection

A user takes one of three scopes from a marketplace:

1. Every package.
2. Named packages.
3. Named features inside a package, where the package allows that split.

A feature is a named part of a package that a user can take alone.

### Versions and pins

A user can pin a marketplace to a specific version. A pin blocks updates. It does not block information.
`fleet check` covers marketplaces. A pinned marketplace still shows that a newer version exists.
`fleet status` shows the cached result of that check.
This design proposes a Git tag or commit as the version. The rulings do not fix it.

### Refresh

A refresh replaces each pulled-in component.
Fleet saves any edited copy in the run backup and prints one line.
A local edit to a pulled-in component is therefore never a conflict.

### Rendering

An installed package's items join the machine's payload.
This design proposes that Fleet renders them as it renders foundation items.
The exception list and the agent choices then apply to them.
A marketplace choice already belongs to one machine. This design proposes that a package item carries no `domains` key.
A pulled-in skill must reach `~/.agents/skills`, where the agents read skills.
This design proposes one link per pulled-in skill, from that folder into the library. The inventory lists each link.
A pulled-in plugin installs through the agent's installer (ruling 7).
This design proposes that a foundation item wins over a pulled-in item with the same key. Fleet reports the skipped item once.

### Catalog file

The rulings fix no file name and no key name.
This sketch shows one possible catalog file. The builder designs the real format in phase 2.

```toml
schema = 1
description = "Shared agent packages for the example team."

[packages.review-kit]
description = "Review skills, one rule set, and a shell guard."
folder = "packages/review-kit"

[packages.review-kit.features.guard]
description = "Stop destructive shell commands."
items = ["hooks.guard-shell", "skills.guard-notes"]

[packages.review-kit.features.style]
description = "Review rules and the release-notes skill."
items = ["rules.review-style", "skills.release-notes"]
```

In this sketch, each package folder holds its skills and one item file.
The item file uses the tables and keys of the `agents` table, without `domains`. One validator can then check both.
A package without features allows no split.

### Trust

A package can carry hooks and `stdio` servers. Both run commands on the machine.
Add only a marketplace that you trust. The content contract gives the same warning for content.
This design proposes that a marketplace update uses the existing update path: review, backup, and run account.

## 8. Gateway

Phase 3 builds the gateway. Phases 1 and 2 do not depend on it.

### What the gateway is

The gateway is one Fleet MCP server that stands between each agent and every declared server.

1. Each agent gets one gateway entry. Fleet sets it once.
2. The gateway serves the tool layer: the tools of every declared server that the machine receives.
3. The gateway also serves read-only content, as MCP prompts and resources.
4. "Served" becomes a render kind, beside the file kinds.
5. Hooks, plugins, settings, and skill folders stay on files.

A prompt is a named text template that a server offers. A resource is a read-only document that a server offers.

### What changes in phase 3

| Agent | Before the gateway | With the gateway |
| --- | --- | --- |
| Claude Code | One entry per server in the tree's plugin folder. | One gateway entry in the same folder. |
| Gemini CLI | One entry per server in the tree's extension folder. | One gateway entry in the same folder. |
| Codex, OpenCode, and Pi | One owned entry per server in the agent's own file. | One owned gateway entry, set once. |

A server change then changes the tree only. This design proposes that the gateway reads its server list from the tree.
Entry-level rendering ends for servers. Connector entries remain, as limit 4 below explains.

### How the evidence shapes it

No test covers a gateway. Every statement about gateway behavior is Unverified until phase 3 tests it.
The evidence in section 2 still sets these limits:

1. Every agent's server format includes a `stdio` entry (Tested or Documented). An agent can therefore start the gateway itself.
   This design proposes that start method. It adds no listener and no background service.
2. This design proposes that the gateway resolves variable names itself. Agents then need no reference form for each server.
   That would remove Antigravity's open reference question.
3. Codex gives a `stdio` server a fixed variable list, plus the names in `env_vars` (Documented in source).
   A new variable name would change the Codex gateway entry. Section 10 records this conflict, and section 11 asks about it.
4. A connector needs a sign-in that each agent keeps in its own store (Documented).
   The gateway cannot use that session. Connectors therefore stay on their phase 1 outputs.
5. Claude Code, Gemini CLI, and OpenCode read MCP prompts (Documented).
   Codex 0.169.0 has no prompt support (Documented in source). Pi, Antigravity, and Grok document none.
6. Claude Code, Gemini CLI, OpenCode, and Pi read MCP resources (Documented).
   Codex's client can list and read them (Documented in source). Antigravity's terminal command and Grok document none.
7. Claude Code and Grok prefix a tool with its server name (Documented). Behind a gateway, every tool comes from one server.
   A permission rule or a hook that names a tool must then use the new name.
8. Gemini CLI disables servers in a folder that the user has not trusted (Tested). The gateway entry follows the same rule.
9. OpenCode stops with an error on an invalid entry (Tested). One stable entry lowers that risk.

## 9. Remote profile management and remote domain assignment

### Dispatch today

Dispatch is Fleet's existing way to run one command on another machine.
It uses SSH, a secure remote login tool. The target's own installed manager does the work.
Fleet 0.5.0 dispatches five actions: status, check, diff, doctor, and update.
A request carries one action and one option. It cannot name a profile, a path, an executable, or another target.
Configure, rollback, and uninstall run only on the machine itself. Configure is the command that starts the wizard.

### Remote profile management

A profile command with a machine name runs on that machine through dispatch.

1. The target's manager is the only writer of its profile.
2. No profile file crosses machines. Only the named change travels.
3. The scope is four profile parts: the domain, the exception list (ruling 9), groups, and tools.
4. The wizard and uninstall stay local.

Rollback also stays local, as it does today. No ruling changes that rule.
This design proposes that the target validates each change against its own content and agent catalog.
The target still disables terminal decisions. An unattended conflict still stops without an overwrite.

### Domains

Three domain rules apply here:

1. Core always applies. Core is the shared content that every machine receives.
2. Each machine carries one domain.
3. Content defines the domains. Users create and remove domains in their own content.

Content declares a domain in the manifest's `overlays` list or in its `domains` table.
The [content contract](CONTENT-CONTRACT.md) holds the exact rules.

### Remote domain assignment

Fleet sets a connected machine's domain by dispatch. A connected machine is a saved machine with an SSH target.
The target's manager writes the new domain into its own profile. The target's next update applies it.
Section 11, item 12 covers when the other profile changes apply.
This design proposes that the target rejects a domain that its content does not declare.

### What this changes in Layer 1

The [Layer 1 decisions](DESIGN.md) hold three rules that this section touches:

| Rule today | Change that this design proposes |
| --- | --- |
| A request carries one action and one option. | A request must also carry one named profile change. Both ends validate it. |
| A request cannot name a profile, a path, an executable, or another target. | A request still names no path, executable, or target. It names one profile part and its new value. |
| No profile transfers between machines. | Unchanged. The target writes its own profile from one validated change. |

This document does not edit the Layer 1 decisions. The builder updates them with the build.
The rulings assign this work to no numbered phase.

## 10. Where the rulings meet the evidence

A ruling sets what Fleet does. The evidence in section 2 shows what each agent allows.
This section lists each place where the two differ, and what this design does there.
No row overrules a ruling. Section 11 asks the owner about the choices that remain.

| Ruling | Evidence | What this design does |
| --- | --- | --- |
| Deployment model: one include line for rules. | Only Claude Code and Antigravity read an include line that can reach the tree (Documented). | Gemini CLI gets a context file in its linked folder. OpenCode gets one settings entry. Pi gets one link. |
| Deployment model: one include line for rules. | Codex has no include syntax (Documented in source). | Codex gets one owned key. A rule change rewrites that key in Codex's own file. |
| Deployment model: a link or shared folder for skills, set once. | Claude Code documents no shared folder. One link per skill is Documented. One link for all skills lacks a session test. | Fleet links each skill. A new skill adds one link in `~/.claude/skills`. |
| Deployment model: hook entries, set once. | Codex keeps hooks in its own file. A removed entry moves later entries, and Codex asks for trust again (Documented in source). | Fleet writes one entry per hook. It appends entries and keeps their order. |
| Deployment model: an update changes only the tree. | Codex trusts a hook entry, not the script that the entry names (Documented in source). | A changed script passes without a new Codex review. Fleet's review before apply is the only review. |
| Ruling 3: skills install at `~/.agents/skills`. | Five agents read that folder, whether Fleet selects them or not (Tested or Documented). | An agent choice cannot hide a skill from those five agents. |
| Ruling 4: prefer a self-contained folder. | A policy can block Claude Code's folder. An allow-list can block Gemini CLI's folder (Documented). | Fleet reports the block. It does not fall back to entries. |
| Ruling 7: uninstall through the agent's installer. | OpenCode 1.17.8 has no removal command (Tested for the help text). | Fleet reports that removal as a manual step (section 11, item 10). |
| Ruling 7: fully managed plugins. | Gemini CLI asks every install to accept a risk warning. Its `--consent` option answers it (Tested for the help text). | Fleet passes `--consent`. The reviewed declaration carries that consent (section 11, item 8). |
| Ruling 7: fully managed plugins. | Claude Code asks a person to confirm a declared install command (Tested for the help text). | Fleet never passes `--yes`. It reports that install as a manual step (section 11, item 8). |
| Ruling 8: a plugin install is the only exception. | An installer writes the agent's settings. Two installers write files that hold Fleet's entries (Documented in source). | Fleet runs each installer first, then reads those files again. |
| Ruling 8: a plugin install is the only exception. | Codex needs a second command before an install: `codex plugin marketplace add` (Tested for the help text). | This design counts that command as part of the install (section 11, item 8). |
| Ruling 7: driven by the record. | Claude Code, OpenCode, and Pi install a listed plugin by themselves at start (Documented). | The inventory can lag behind the agent. Fleet checks the agent's own plugin records at each update (section 11, item 9). |
| Ruling 7: uninstall, driven by the record. | Removing a plugin marketplace uninstalls every plugin from it, in Claude Code and Grok (Documented). | Fleet never removes a plugin marketplace. |
| Gateway: one entry, set once. | Codex passes a `stdio` server only the variables that the entry names (Documented in source). | A new variable would change the entry, unless the gateway reads `~/.secrets` itself (section 11, item 13). |
| Gateway: serves read-only content. | Codex 0.169.0 reads no MCP prompts (Documented in source). Pi, Antigravity, and Grok document none. | A served prompt reaches Claude Code, Gemini CLI, and OpenCode only. |
| Gateway: serves the tool layer. | A connector needs a sign-in that the agent keeps (Documented). | Connectors stay on their phase 1 outputs. |

Two rulings also meet each other. Under ruling 3, skills install at `~/.agents/skills`, outside the tree.
The deployment model puts the full configuration in the tree.
This design treats that shared folder as the skills part of the payload.

## 11. Open after the rulings

The rulings leave some points open. For other points, this document had to interpret a ruling.
The first list holds the points that need the owner. Each one carries a recommendation.
The second list holds the smaller proposals in this document. The owner can overrule any of them.
The owner ruled on twelve points on 2026-11-20: 1, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, and 16.
Points 2, 3, 6, and 12 stay open. Claude Code's part of point 7 and the restore part of point 11 also stay open.
The build made a choice for most points. Each ruled point keeps its recommendation as the record of the advice given.

Each point carries one mark for `main` at commit `2f1e154`. The mark names the file that proves it:

- BUILT AS RECOMMENDED: `main` does what the recommendation says.
- BUILT DIFFERENTLY: `main` covers the point but does something else. The mark says what.
- NOT BUILT: no code on `main` covers the point.

A mark records the build, not a ruling. A line that starts with RULED states the owner's ruling.
A mark that differs from a ruling shows work that the build still needs.

### Points for the owner

1. What happens to a hand-written or hand-edited value at Fleet's target in an agent's file?
   Option A is the first draft's rule: ask in a terminal, and stop an unattended run. Appendix C holds it.
   Option B keeps the value, releases any record, and reports once. It never prompts and never stops a run.
   Under option B, Fleet also never takes over an equal value. The main text uses option B.
   Recommendation: option B. It follows "no prompt, no refusal", and it matches ruling 6 for a changed stale output.
   BUILT DIFFERENTLY: a differing value is a conflict that stops every run before backup, without a prompt.
   An unrecorded equal value is taken over. A `local` exception resolves the conflict. Proof: `scripts/agent_deploy.py`, `scripts/manager.py`.
   RULED 2026-11-20: option B. Fleet keeps the user's value, stops managing that setting, and prints one line. The run continues.
2. What happens to a hand edit inside the tree?
   Option A uses the file conflict path, as a changed managed file does today.
   Option B replaces the file, saves the edited copy in the run backup, and prints one line.
   Recommendation: option B. The tree is generated, and the foundation is the only conflict surface.
   BUILT AS RECOMMENDED: option B. Proof: `scripts/agent_deploy.py`.
3. What does a `local` exception hold, and how far does it reach?
   This design reads "an item with a local value" as a marker. The profile names the item and never stores the value.
   The marker covers every agent on the machine.
   Recommendation: keep the marker without the value, because a hand-written value can hold a secret.
   Add a form for one agent only when a case needs it.
   BUILT AS RECOMMENDED: the profile stores `local` under the item's key, for every agent. Proof: `scripts/agent_table.py`, `scripts/agent_deploy.py`.
4. May an item's `domains` list name `core`?
   As built from the first draft, `core` in a list selects the machines whose own domain is core.
   "Core always applies" points the other way: a `core` item would reach every machine.
   Recommendation: reject `core` in a list. An item for every machine omits `domains`.
   BUILT DIFFERENTLY: validation accepts `core`, and it selects only machines whose domain is core. Proof: `scripts/agent_table.py`.
   RULED 2026-11-20: Fleet rejects `core` in a `domains` list with an error. An item without `domains` reaches every machine.
5. Does Claude Code get one link per skill, or one link for all skills?
   The single link runs through the plugin folder. It also changes each skill's name to `/fleet:NAME`.
   Recommendation: one link per skill in the first build. Switch only after a session test passes.
   BUILT DIFFERENTLY: one link, `~/.claude/skills/fleet`, leads to the plugin folder. That folder links to `~/.agents/skills`.
   Proof: `scripts/agent_deploy.py`. This document holds no record of a session test.
   RULED 2026-11-20: one link per skill in `~/.claude/skills`. A skill keeps the name `/NAME` in every agent.
6. Does Codex get one hook entry per hook, or one entry per event that runs a Fleet script?
   The second form is set once. Fleet's script must then match tools and merge the hooks' results.
   Recommendation: one entry per hook. Codex then keeps its own matching, time limits, and exit codes.
   BUILT AS RECOMMENDED: one owned entry per hook in `~/.codex/hooks.json`. Proof: `scripts/agent_deploy.py`.
7. Which rule pointer does an agent get where the include line does not fit?
   Codex can take the `developer_instructions` key, or a session-start hook that prints the rules.
   Recommendation for Codex: the key. It needs no trust step, and a rule change rewrites one key.
   Claude Code can take the include line, or one link in `~/.claude/rules`.
   Recommendation for Claude Code: build the include line first, as ruled. Move to the link after one session test.
   The link changes no shared file. Content can then still manage `CLAUDE.md`.
   Recommendation for Pi: the link at `APPEND_SYSTEM.md`, after one test of a linked file.
   BUILT DIFFERENTLY: Codex and Pi receive no rule pointer. Fleet reports their rule sets as unsupported.
   Claude Code receives the link `~/.claude/rules/fleet.md`, not an include line. Proof: `scripts/agent_deploy.py`.
   RULED 2026-11-20 for Codex and Pi: Codex gets the rules in its `developer_instructions` setting.
   Pi gets a link at `~/.pi/agent/APPEND_SYSTEM.md`, after one test of a linked file.
   The ruling does not cover Claude Code. Its part of this point stays open.
8. Which agent commands does a plugin install include, and which questions may Fleet answer?
   Codex needs `codex plugin marketplace add` before an install. Claude Code adds a plugin marketplace inside its install command.
   Gemini CLI asks every install to accept a risk warning. Its `--consent` option answers that question.
   Claude Code asks about an install command that a plugin marketplace declares. Its `--yes` option answers that question.
   Recommendation: count the Codex command as part of the install.
   Pass `--consent`. The reviewed declaration carries that consent, and no managed install works without it.
   Never pass `--yes`. It accepts a command that no person saw. Report that install as a manual step.
   A later schema could carry the reviewed command's hash. Fleet could then pass `--accept-command`.
   Report a Gemini CLI extension from a local path as a manual step, because it can ask about folder trust.
   BUILT DIFFERENTLY: Fleet runs `codex plugin marketplace add` before `codex plugin add`, and it passes `--consent`.
   Fleet runs no Claude Code installer, so `--yes` never arises. A Gemini CLI install from a local path fails the run.
   Proof: `scripts/agent_deploy.py`.
   RULED 2026-11-20: Fleet omits a Gemini CLI extension install from a local folder. It lists a manual step, and the run continues.
9. What happens when an agent already holds a declared plugin that Fleet did not install?
   Recommendation: treat it as the user's own. Record nothing, never uninstall it, and report it once.
   Fleet learns this from the agent's plugin records on disk. It runs no agent command for that check.
   NOT BUILT: Fleet reads no agent plugin records. It plans every declared plugin as its own and records the install.
   Proof: `scripts/agent_deploy.py`, function `plan_plugins`.
   RULED 2026-11-20: a plugin that the user installed stays the user's. Fleet records nothing, never uninstalls it, and prints one line.
10. How does Fleet remove an OpenCode plugin? Version 1.17.8 has no removal command.
    Option A reports a manual step. Option B removes the module from the `plugin` list by a file edit.
    Recommendation: option A first. Move to option B after a test shows that OpenCode stops loading the module.
    BUILT DIFFERENTLY: option B. Fleet removes the module from the `plugin` list. Proof: `scripts/agent_deploy.py`.
    RULED 2026-11-20: option B. Fleet removes an OpenCode plugin from OpenCode's plugin list. `main` already does this.
11. Do `fleet uninstall` and rollback remove the plugins that Fleet installed?
    Recommendation: no. Uninstall keeps installed tools today. Keep the plugins, and list each removal command.
    BUILT DIFFERENTLY: uninstall and restore run no plugin installer, and they list no removal command.
    Uninstall does remove Fleet's listing entries, so Claude Code, OpenCode, and Pi stop loading those plugins.
    Proof: `scripts/agent_deploy.py`, function `removal_operations`.
    Correction: the mark of 2026-11-20 said that uninstall keeps every plugin. It keeps only those that Codex and Gemini CLI installed.
    RULED 2026-11-20, against the recommendation: `fleet uninstall` uninstalls the plugins that Fleet installed. It prints no command for the user.
    The ruling does not cover a backup restore. That part stays open.
12. When does a remote profile change apply, and does a remote domain count as a person's choice?
    The ruling says that the target's next update applies a remote domain.
    Fleet records whether a person chose each machine's domain.
    Recommendation: apply every remote profile change at the target's next update. Record a remote domain as chosen, because a person set it.
    BUILT AS RECOMMENDED: the target saves the change, and its next update applies it. A remote domain is recorded as chosen.
    Proof: `scripts/command.py`, function `save_remote_choice`.
13. How does the gateway start, and how does it read variables?
    Recommendation: each agent starts it as a `stdio` server. The gateway reads `~/.secrets` itself.
    The Codex entry then never changes for a new variable. Connectors stay on their phase 1 outputs.
    BUILT DIFFERENTLY: each agent starts the gateway as a `stdio` server, and connectors keep their own entries.
    Each agent's gateway entry names every variable that a server reads. A new variable changes the entry.
    Proof: `scripts/agent_deploy.py`, function `gateway`; `scripts/gateway.py`.
    RULED 2026-11-20: the gateway reads `~/.secrets` itself.
14. What is the marketplace feature called? The name does not block the build. The owner picks it before phase 2 ships.
    Recommendation: avoid the bare word marketplace. Claude Code and Codex already use it for their plugin catalogs.
    BUILT DIFFERENTLY: phase 2 shipped as `fleet marketplaces`. Proof: `scripts/command_arguments.py`, `docs/MARKETPLACES.md`.
    RULED 2026-11-20, against the recommendation: keep the name `fleet marketplaces`. `main` already does this.
15. How does a bridge plugin reach OpenCode and Pi? The phase 4 study answers this.
    Recommendation: study a linked code file first. Both agents load code files from a folder (Documented).
    NOT BUILT: no bridge code exists. The study recommends rendering each bridge into the tree, behind one pointer.
    Proof: `docs/BRIDGE-SPIKE.md`. That study asks the owner four questions of its own.
    RULED 2026-11-20: the study's option A. Fleet copies each bridge into `~/.fleet/agents` and links it.
    The study's other three questions stay open.
16. How does Fleet install a plugin for Claude Code, OpenCode, and Pi? The build raised this point.
    Ruling 7 says that Fleet installs through the agent's own installer.
    Option A runs each agent's installer command. Option B writes an entry in the agent's plugin listing. The agent then installs the plugin itself.
    Option B runs no agent command, so it also fits ruling 8. Claude Code installs a listed plugin only at an interactive start.
    Recommendation: option B. Record that a listing entry counts as the agent's installer under ruling 7.
    BUILT DIFFERENTLY from the ruling's wording: option B. Proof: `scripts/agent_deploy.py`, constant `LISTED`; `docs/AGENTS.md`.
    RULED 2026-11-20: option B. The plugin listing counts as the agent's own installer under ruling 7. `main` already does this.

### Smaller proposals

| Proposal | Section | Mark on `main` | Proof |
| --- | --- | --- | --- |
| The builder chooses the tree's path and layout. | 2 | BUILT DIFFERENTLY: `~/.fleet/agents`, with agent folders and kind folders. | `scripts/agent_deploy.py` |
| Fleet builds one combined rule file per agent in the tree. | 2 | BUILT AS RECOMMENDED | `scripts/agent_deploy.py` |
| Fleet never removes a plugin marketplace. | 2 | BUILT AS RECOMMENDED | `scripts/agent_deploy.py` |
| A blocked self-contained folder gets no fallback entries. | 2 | NOT BUILT: Fleet checks no policy or allow-list. | `scripts/agent_deploy.py` |
| The library sits outside the user's tracked content, in a folder that the builder chooses. | 3 | BUILT AS RECOMMENDED: `~/.fleet/marketplace-packages`. | `scripts/marketplaces.py` |
| Recorded skill links stay until content moves its skills. | 4 | BUILT DIFFERENTLY: the next update removes each recorded link that still matches its record. | `scripts/agent_deploy.py` |
| A changed stale output keeps a record that marks it as kept. | 4 | BUILT DIFFERENTLY: a kept path loses its record. A kept entry keeps its record. | `scripts/agent_deploy.py` |
| An `off` exception makes an item's outputs stale. | 4 | BUILT AS RECOMMENDED | `scripts/agent_table.py` |
| Fleet lists plugin changes in the review and records only a successful install. | 4 | BUILT AS RECOMMENDED | `scripts/agent_deploy.py` |
| Fleet runs installers after the backup and before the entry writes. | 4 | BUILT DIFFERENTLY: installers run after the entry writes. | `scripts/agent_deploy.py` |
| Fleet skips a shared file that it cannot edit safely. It does not stop the run. | 4 | BUILT DIFFERENTLY: an unsafe shared file stops the run. | `scripts/agent_deploy.py` |
| Fleet identifies its include line by the line's exact text. | 4 | NOT BUILT: Fleet writes no include line. | `scripts/agent_deploy.py` |
| A marketplace version is a Git tag or commit. | 7 | BUILT AS RECOMMENDED | `scripts/marketplaces.py` |
| A marketplace choice belongs to one machine. A delete also removes that marketplace's installed packages. | 7 | BUILT AS RECOMMENDED | `scripts/marketplaces.py`, `scripts/command.py` |
| One link per pulled-in skill leads from `~/.agents/skills` into the library. | 7 | BUILT DIFFERENTLY: the link leads to a copy in the tree. | `scripts/agent_deploy.py` |
| A foundation item wins over a pulled-in item with the same key. | 7 | BUILT DIFFERENTLY: a shared name stops the run. | `scripts/marketplaces.py` |
| A package item carries no `domains` key. The exception list and the agent choices apply to it. | 7 | BUILT AS RECOMMENDED | `scripts/marketplaces.py`, `scripts/agent_table.py` |
| A marketplace update uses the existing update path: review, backup, and run account. | 7 | BUILT AS RECOMMENDED | `scripts/command.py` |
| The gateway reads its server list from the tree. | 8 | BUILT AS RECOMMENDED | `scripts/gateway.py`, `scripts/agent_deploy.py` |
| A remote request carries one named profile change. The target validates it against its own content. | 9 | BUILT AS RECOMMENDED | `scripts/remote.py`, `scripts/command.py` |
| The shared skills folder counts as the skills part of the payload. | 10 | BUILT DIFFERENTLY: the payload is the tree. `~/.agents/skills` holds content and links into the tree. | `scripts/agent_deploy.py` |

## Appendix A: Build phases

The rulings set four phases. They replace the build order that the first draft suggested.

1. Phase 1 builds the module core:
   - The `agents` table and its validation.
   - The store split by owner, and the foundation rules.
   - The tree and the per-agent pointers.
   - Skills as ordinary content (ruling 3).
   - The exception list (ruling 9).
   - The agent catalog changes (ruling 12).
   - Managed plugins (ruling 7).
   - Unsupported hooks for OpenCode and Pi (ruling 10).
2. Phase 2 builds marketplaces: the format, the commands, selection, pins, and check coverage (section 7).
3. Phase 3 builds the gateway: one gateway entry per agent, and the served render kind (section 8).
4. Phase 4 builds the read-only import (ruling 11) and writes a design study for bridge plugins.

The bridge study is a document for the owner's review, not a build.
A bridge plugin is one Fleet-shipped plugin for one agent. It reads the shared hook declarations from the tree.
It then registers those hooks inside the agent. Its delivery method is open.
Remote profile management and remote domain assignment have no phase number.

## Appendix B: Evidence record

The author ran every test on 2026-11-17 on one Linux machine under WSL. Two test passes ran that day.
The first pass supported the first draft. The second pass checked the rulings against the installed agents.
Each test set the home folder to a new empty folder. No real agent settings changed.
The author also read key names, never values, in the machine's real settings files.
Each version comes from the binary's own version output.
No test started an agent session. No test installed a plugin.

| Agent | Version | First pass | Second pass |
| --- | --- | --- | --- |
| Claude Code | 2.1.326 | `claude mcp add`; `claude plugin init`, `list`, `details`, and `validate`; a linked plugin folder; variable expansion. | A linked `skills` folder inside the plugin folder. Help text for plugin install, uninstall, list, and marketplace add. |
| Codex | 0.169.0 | `codex mcp add`, `list`, and `get`; hand-written variable-name keys; comment survival. | Help text for `codex plugin add`, `remove`, `list`, and `marketplace add`. |
| Gemini CLI | 0.50.0 | `gemini mcp add` and `list`; `skills list` with links; a hand-placed extension; `hooks migrate`. | A linked extension folder with a context file. Help text for extension uninstall and list. |
| OpenCode | 1.17.8 | `opencode debug config` with three settings files; `debug skill` with three folders; an invalid entry. | The command list, which holds no plugin removal command. An `instructions` entry in `debug config`. |
| Pi | 1.3.0 | `pi mcp add` and `list`; the documentation that ships inside the package. | Help text for `pi remove` and `pi list`. |
| Antigravity | Not installed | Nothing. Vendor documentation only. | Nothing. Vendor documentation only. |
| Grok Build | Not installed | Nothing. Vendor documentation and source only. | Nothing. Vendor documentation and source only. |

The first pass also captured help text for `gemini extensions install`, `opencode plugin`, and `pi install`.

Gemini CLI and Pi wrote files into their folders during a help command (Tested). OpenCode created folders (Tested).
Claude Code wrote `~/.claude.json` during `claude plugin list` (Tested).
Agent folders change without a user action. This is why Fleet owns a tree of its own, not whole settings files.

Documented claims come from these sources, read on 2026-11-17:

- Claude Code: `code.claude.com/docs`, pages for skills, plugins, MCP, hooks, settings, and memory.
- Codex: `learn.chatgpt.com/docs` and the `openai/codex` source at tag `rust-v0.169.0`.
- Gemini CLI: `geminicli.com/docs` and the `google-gemini/gemini-cli` source at tags `v0.50.0` and `v0.68.0`.
- Antigravity: `antigravity.google/docs`, pages for rules, plugins, and MCP.
- OpenCode: `opencode.ai/v1/docs` and the `anomalyco/opencode` source at tag `v1.17.8`.
- Pi: `docs.pi.dev`, the documentation inside package version 1.3.0, and the `earendil-works/pi` source at tag `v1.3.0`.
- Grok: `docs.x.ai/build` and the `xai-org/grok-build` source. That source has no version tags.
- Skills: `agentskills.io/specification`.

Three Codex claims rest on these source files at that tag:

- `codex-rs/rmcp-client/src/utils.rs` builds the variable list for a `stdio` server.
- `codex-rs/hooks/src/lib.rs` builds the key of a hook's trust record.
- `codex-rs/hooks/src/engine/discovery.rs` builds the hash of that record.

## Appendix C: The first draft's conflict rule

Section 11, item 1 names this rule as option A. The main text does not use it.
The first draft called two cases a conflict: an unrecorded value that differs, and a recorded output that a person changed.

Fleet asks about each conflict during apply, after the backup. File conflicts already ask at that point.
The prompt reads the terminal, never piped input. It uses the same letters as the file prompt:

```text
Agent conflict
--------------
codex: MCP server issues in ~/.codex/config.toml differs from the rendered value.
Type one letter, then press Enter.
Uppercase S differs from lowercase s.

  d  Show changes for this item, then ask again.
  o  Replace this item with the rendered version.
  a  Replace this item and all remaining conflicts without asking again.
  s  Keep this item unchanged; continue with other items.
  S  Keep all conflicting items unchanged; apply other changes.
  q  Stop applying. Changes already made remain in place.

Choose [d/o/a/s/S/q]:
```

A skipped item stays in conflict. The run then ends as incomplete with exit code 1, as a skipped file conflict does today.
The inventory records each unresolved item. Status and doctor report it until a later run resolves it.
Invalid input repeats the prompt. Tests must drive this prompt through a pseudo-terminal, which simulates a user's terminal.
Without a terminal, a conflict or a changed output stops the run before the first write.
Fleet lists each item, its file, and the fix. Fleet never chooses overwrite for the user.
An unrecorded value that equals the rendered value becomes managed, without a write. The run account lists it.
