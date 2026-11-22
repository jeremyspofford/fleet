# Agent configuration

An agent is an installed coding assistant, such as Claude Code or Codex.
The content repository declares agent configuration in the `agents` table of `fleet.toml`. See [the content contract](CONTENT-CONTRACT.md#agents-table).
The catalog, `agents/manifest.toml`, lists each agent's detection commands and native paths. The profile saves which agents are on.
Fleet installs and starts no agent. Agent sign-in remains separate.

## Choose agents

`PATH` lists directories searched for commands. Detection checks PATH, installed Mise packages, and known configuration folders. Any match detects the agent.
Turn each detected agent on or off during enrollment. Press Enter to keep its current choice.
Before the first choice, the step counts the skills, MCP entries, rules, and other items your content declares.
When the content declares none, the step says so. On or off then changes nothing until you add some.
On lets Fleet manage the agent's configuration. Fleet writes only entries it owns and never touches the agent's sign-in.
Off leaves the agent's configuration alone, and the agent still works.
For scripted runs, use `--agents claude,codex` for detected agents. Use `--agents none` to skip.
Turning an agent off writes nothing new for it. Its earlier outputs stay for review.

## Deployment tree

Fleet deploys one payload per machine: the agent configuration in a Fleet-owned tree, `~/.fleet/agents`.
Updates change the tree. Each agent gets one pointer to the tree, set once:

| Agent | Pointer | Contents |
| --- | --- | --- |
| claude | `~/.claude/skills/fleet` links to the tree's plugin folder. Each skill gets `~/.claude/skills/NAME`, linked to `~/.agents/skills/NAME`. `~/.claude/rules/fleet.md` links to its rule file. | The gateway entry, connectors, and hooks. |
| gemini | `~/.gemini/extensions/fleet` links to the tree's extension folder. | The gateway entry, connectors, hooks, and a `GEMINI.md` rule file. |
| codex | Skills: Codex reads `~/.agents/skills` itself. Servers: one gateway entry in `~/.codex/config.toml`. | Owned connector entries and hook entries in `~/.codex/hooks.json`. |
| opencode | Skills: OpenCode reads `~/.agents/skills` itself. Rules: one `instructions` entry names the tree's rule file. Servers: one gateway entry. | Owned connector entries in `~/.config/opencode/opencode.json`. |
| pi | Skills: Pi reads `~/.agents/skills` itself. Servers: one gateway entry in `~/.pi/agent/mcp.json`. | Owned connector entries in the same file. |
| antigravity | Servers: one gateway entry in `~/.gemini/config/mcp_config.json`. | Nothing else. Its other surfaces are untested; see Paths and limits below. |

After the pointer, Fleet does not write inside an agent's own files.
Codex, OpenCode, Pi, and Antigravity accept no pointer for servers or hooks. They receive owned entries instead.
An owned entry is one named value that Fleet writes inside a file that the agent also writes.

Claude Code loads the plugin folder as `fleet@skills-dir`. Its servers appear as `plugin:fleet:NAME`.
Each skill keeps its own name, `/NAME`, as in every other agent. Claude Code 2.1.259 listed a linked skill that way in a test on 2026-11-22.
A skill folder may not be named `fleet`, because Fleet's plugin link uses that name.
A permission rule that names one of those tools must use the longer plugin name.
The tree belongs to Fleet. An update replaces hand edits there after the backup copies them.

## Skills

A skill is a folder of instructions for an agent. Its main file is `SKILL.md`.
Skills are ordinary content. Put each one at `.agents/skills/NAME/` in the content repository. chezmoi installs it at `~/.agents/skills`.
Codex, Gemini CLI, OpenCode, and Pi read that folder. Claude Code reads it through the plugin folder's link.
An `agents.skills.NAME` entry limits a skill to listed domains. The exception list can also turn one skill off on one machine.
Native chezmoi content installs every skill. Use `.chezmoiignore` there to limit skills.

To move skills from an earlier layout:

1. Move each skill folder from `agents/skills/` to `.agents/skills/` in the content repository.
2. Remove `agents/skills` from the manifest's `paths` list.
3. Commit, push, and run `fleet update`.

Earlier releases linked each skill into each agent's folder. The update removes each recorded link that still matches its record.

## Servers, connectors, rules, and hooks

MCP means Model Context Protocol. An MCP server offers tools, prompts, and resources to an agent.
Fleet serves every declared MCP server through its gateway. Served is a render kind beside the file kinds.
Each agent receives one server entry, named `fleet`. It runs `~/.fleet/agents/gateway/run-AGENT`.
That wrapper starts `scripts/gateway.py` in the manager with the agent's gateway file from the tree.

The gateway starts each downstream server on demand. It serves their tools and prompts as `SERVER__NAME`.
It serves their resources for reading only. A server that fails to start is skipped; the others keep working.
Downstream stdio servers start with the gateway's environment plus their `env` values.
The gateway entry names no variable. The gateway reads `~/.secrets` itself, so a new server changes no agent settings file.
Each server gets only the `pass_env`, `header_env`, and `bearer_token_env` values it declares. A variable in the gateway's own environment wins.
The gateway reads `export NAME=VALUE` and `NAME=VALUE` lines with shell quoting rules. It runs nothing.
It refuses a `~/.secrets` that another user can read or owns, and logs one line without any value. A missing file is no error.
Antigravity expands no reference. Its servers inherit its environment, so its entry names no variable.
Fleet never writes a secret value. The wrapper finds `python3` through the agent's `PATH`.
Hooks, plugins, settings, and skill folders stay on files.

A connector with a `url` renders as an HTTP server entry, because the agent itself handles the sign-in. Next steps lists the agent's sign-in command.
A connector cannot be named `fleet`; the gateway uses that name.
A connector without a `url` appears in Next steps as a manual step.

Rule sets combine into one file per agent in the tree. Codex and Pi read no include file.
Codex receives the combined rule text in its top-level `developer_instructions` setting in `~/.codex/config.toml`.
Pi receives a link at `~/.pi/agent/APPEND_SYSTEM.md` to its rule file in the tree. Pi 1.3.0 reads that linked file, tested on 2026-11-20.
Whole instruction files, such as `AGENTS.md`, stay ordinary content. Install them under their real names.

Each hook runs a wrapper in the tree. The wrapper sets `FLEET_AGENT`, `FLEET_HOOK`, and `FLEET_HOOK_EVENT`, then runs a copy of the hook script.
Gemini CLI counts hook timeouts in milliseconds. Fleet converts seconds for it.
A tool class without a confirmed matcher for an agent skips that hook for that agent. The run account says so.
Codex skips a new or changed hook until the user trusts it in `/hooks`. Next steps lists that step.
OpenCode and Pi run hooks only through code plugins. Fleet reports their hooks as unsupported. The [bridge plugin spike](BRIDGE-SPIKE.md) proposes a later fix for owner review.

OpenCode merges `opencode.jsonc` after `opencode.json`. A same-name server there overrides Fleet's entry.
Fleet then skips that server for OpenCode and reports the override. The run continues.

## Per-machine exceptions

An item is one declared skill, rule set, server, connector, plugin, or hook. Its name is `KIND.NAME`, such as `mcp.docs`.
The profile keeps this machine's exception list under `agent_items`. Content never holds it.

1. Run `fleet agents list` to see each item, the agents and domains it reaches, and its state here.
2. Run `fleet agents set --name ITEM --state STATE`. The command saves the profile, then applies the change.

| State | Effect on this machine |
| --- | --- |
| `on` | Fleet manages the item. This removes the item's exception. |
| `off` | Fleet removes its output for the item and renders it no longer. |
| `local` | Fleet stops managing the item. The current value stays, and Fleet never reports it as drift. |

Skills accept `on` and `off` only; they are content files. Updates honor the list.

## Import existing settings

Run `fleet agents import --agent NAME` to print agents-table entries from one agent's user-level settings.
NAME is claude, codex, gemini, opencode, or pi. The command reads files and writes nothing.

1. Review the printed TOML and its notes.
2. Copy the entries you want into the `agents` table of `fleet.toml`.
3. Commit, push, and run `fleet update`.

The import never prints a secret value. A value that looks secret becomes a variable name, and a note names it.
Set each named variable in your environment, for example through the content's `secrets` table.
Variable references, such as `${NAME}` or `{env:NAME}`, become `pass_env`, `header_env`, or `bearer_token_env` names.
Fleet's own gateway entry, plugin folder, and hook wrappers are outputs, so the import skips them.
Hooks become notes: move each command into a tracked script, then declare it. OpenCode and Pi hooks are code, so the import cannot read them.

## Drift

Drift means a recorded output differs from its record or is missing.
`fleet status` counts agent drift in its Drift line. `fleet doctor` fails its managed-files check.
`fleet diff` prints one patch per changed output. It exits 1.
The update gate treats agent drift as a drift repair trigger. A missing output returns.
A changed output stays the user's: the update keeps it, stops managing it, and prints one line.
Outputs of an agent that is off never count as drift.

## Plugins

A plugin is a package that an agent installs to add skills, commands, hooks, or servers.
Fleet fully manages declared plugins. Where an agent installs a listed plugin by itself, Fleet writes only the listing.

| Agent | Fleet writes or runs | Removal |
| --- | --- | --- |
| claude | `enabledPlugins` and `extraKnownMarketplaces` entries in `~/.claude/settings.json` | Fleet removes the entries. Claude Code stops loading the plugin and keeps its cached copy. |
| opencode | One `plugin` list entry in `~/.config/opencode/opencode.json` | Fleet removes the entry. OpenCode stops loading the module and keeps its cached copy. |
| pi | One `packages` list entry in `~/.pi/agent/settings.json` | Fleet removes the entry. Pi stops loading the package. |
| codex | `codex plugin marketplace add MARKETPLACE`, then `codex plugin add ID` | `codex plugin remove ID` |
| gemini | `gemini extensions install ID --consent` | `gemini extensions uninstall NAME`, where NAME is the last part of ID |

Listing entries follow the owned-entry rules below, including drift and the exception list.
A plugin that the agent already lists, or already installed, stays yours. Fleet records nothing for it and prints one line.
Fleet reads Codex's `[plugins."ID"]` tables and Gemini CLI's `~/.gemini/extensions/NAME` folders for this check. It runs no agent command.
A later removed declaration uninstalls nothing for such a plugin.
Claude Code installs a listed plugin during an interactive start, in the background. The plugin loads from the next session.
A headless `claude -p` run does not install it. OpenCode and Pi install a listed plugin at their next start.
Next steps names each agent's step in the run that first writes the listing.
A Claude Code marketplace that names `owner/repo` becomes a GitHub source. A URL becomes a Git source; an absolute path becomes a folder source.

Codex and Gemini CLI still run their installers. Fleet's review is the consent step; Gemini CLI's own confirmation is skipped with `--consent`.
Gemini CLI asks to trust a local-path source, so Fleet never installs one. Each run lists the install command as a manual step.
A local source starts with `/`, `~`, or `.`, or names an existing folder. Once you install it, the plugin is yours.
The run account lists installer runs under Software. Backups copy each agent's settings file first.
Fleet runs no other agent command to write settings.

An installer runs when a plugin is declared for Codex or Gemini CLI while that agent is on. Changing a plugin's `id` or `marketplace` removes the old install first.
A removed declaration or a domain change removes the plugin. Turning an agent off keeps its plugins.
The `local` exception releases a plugin: it stays installed, and Fleet stops managing it.
A failed installer stops the run. The inventory records only completed installs and removals.
Earlier releases ran installers for Claude Code, OpenCode, and Pi. The next update releases those records without uninstalling, then writes the listing.

`fleet uninstall` uninstalls the Codex and Gemini CLI plugins that the inventory records. The review lists each one under Software.
Removing Fleet's listing entries uninstalls the Claude Code, OpenCode, and Pi plugins. A plugin that Fleet never recorded stays.
A `local` exception keeps its plugin. Uninstall never removes a marketplace.
A missing agent command or a failed removal prints one line. Uninstall then removes everything else.

## Ownership, drift, and removal

`~/.fleet/agents.json` is the inventory. It lists each owned path, each owned entry, and the keys Fleet wrote.
Fleet compares only the keys that it wrote. Keys that an agent adds inside an entry never count as drift.

| Finding | Action |
| --- | --- |
| Nothing exists at the target | Create the output and record it. |
| An unrecorded value equals the rendered value | Leave it unrecorded. Fleet writes nothing. |
| An unrecorded value differs | Keep the user's value. Fleet writes and records nothing. |
| A recorded output changed | Keep the user's value. Fleet drops its record. |
| A recorded output is missing | Create it again. |

Each kept value prints one line in the run account, with the agent, the item, and the file. Later runs stay quiet about it.
No case stops a run or asks a question, so unattended updates complete. Tree files are Fleet's: an edited tree file is replaced.
Delete a kept value to hand it back. The next update writes Fleet's value and records it.

An output becomes stale when its declaration disappears or no longer selects this machine.
Fleet removes a stale output that still matches its record. A changed stale output stays, with one report line.
Fleet never deletes an agent folder recursively. It never deletes a shared settings file.

Shared files follow these rules:

1. Fleet parses the file first. It stops on invalid content or a link that leaves home.
2. Fleet changes only its own entries. It rewrites Codex's TOML table at the end of the file and keeps every other byte.
   A top-level key, such as `developer_instructions`, goes before the first table.
3. Fleet stops when a comment sits inside its own TOML table. Move the comment outside that table.
4. Fleet parses the result. It stops unless every other value equals its earlier value.
5. Fleet writes atomically and keeps the file's permissions.
6. Fleet refuses a file that content also manages. Two sources cannot own one output.

Uninstall removes owned paths and Fleet's entries. It keeps each shared file and every other value in it.
Restore returns Fleet's entries from the backup. It keeps values that the agent or user wrote after that backup.

To preview the plan from a separate Fleet checkout, run `bash scripts/generate-agent-configs.sh`. It writes nothing.

## Paths and limits

| Agent | Status |
| --- | --- |
| [claude](https://code.claude.com/docs/en/settings) | Rendered through the plugin folder. |
| [codex](https://learn.chatgpt.com/docs) | Rendered through owned entries. |
| [gemini](https://geminicli.com/docs/cli/skills/) | Rendered through the extension folder. |
| [opencode](https://opencode.ai/docs/config/) | Rendered through owned entries. |
| [pi](https://docs.pi.dev) | Rendered through owned entries. |
| [grok](https://docs.x.ai/build/settings) | Detected only; Fleet writes nothing. |
| [antigravity](https://antigravity.google/docs) | Rendered for servers only, through one owned entry. |
| [Ollama](https://docs.ollama.com/faq) | Unsupported. |

Grok is detected only. Fleet renders nothing for it until a tested install confirms its paths.
A community tool also installs a `grok` command; its `~/.grok/user-settings.json` file rules out Grok detection.

Antigravity's command is `agy`. Mise lists it as `agy`, `antigravity-cli`, or `aqua:google-antigravity/antigravity-cli`.
A test with agy 1.5.10 on 2026-11-18 confirmed the server entry without a sign-in. agy started the gateway and listed its tools.
agy creates an empty `mcp_config.json` at its first start. Fleet reads an empty shared file as one with no values.
Rule sets, hooks, connectors, and plugins for Antigravity appear as run notes. Fleet writes nothing for them.
Their paths need a signed-in session to confirm. Antigravity's reading of `~/.agents/skills` is also unconfirmed.
Pi detection also matches its renamed package, `@earendil-works/pi-coding-agent`.

Ollama is unsupported: it uses process/service environment settings, not a portable configuration file.
Service setup differs across Linux's systemd, macOS's launchctl, and Windows.
Fleet reports this limit; it invents no file or skill loader.

Paths cover Linux, macOS, and WSL (Windows Subsystem for Linux). WSL configures Linux agents, not Windows executables.
