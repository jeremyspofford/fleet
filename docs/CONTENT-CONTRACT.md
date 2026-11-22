# Content contract

Fleet is the manager. A content repository holds home files. Git stores history; chezmoi applies files; Mise selects tool versions.
A profile is the machine's saved repository, domain, identity, tools, and group choices.
A tool selection records a version. A group selects optional files or marked blocks.

## Formats

1. Read the root `fleet.toml`, or `dotfleet.toml` when the new name is absent. If both exist, use only `fleet.toml`.
2. Read its `format` field first. It must be `plain` or `chezmoi`.
3. Without that field, detect chezmoi markers such as `.chezmoi*`, `dot_*`, or `private_dot_*`.
4. Otherwise, detect literal home files, including Stow packages under `packages/<package>/`.
5. If detection remains unclear, ask. `--format plain` selects real names; `--format chezmoi` selects native source names.

Plain content uses real names, such as `.zshrc` and `.config/starship.toml`. It contains no chezmoi templates or installer.
Fleet converts plain content into `~/.fleet/chezmoi-source`.
Chezmoi content already uses native source names. Fleet uses that checkout directly; it does not rewrite it.
Native templates and hooks can execute commands. Use only trusted repositories.

## Included files

Only tracked Git files convert. The foundation is the tracked content that Fleet installs, plus the manifest.
Uncommitted edits to foundation files block setup and updates. Commit or move them first.
Unmanaged files, such as untracked notes, never block and never change. Update does not mention them.
An update that would change an unmanaged file with local edits stops before backup and names the file.
Include root dotfiles, dot directories such as `.config`, and paths explicitly declared in `paths`.
Exclude Git metadata, `.github`, `.gitignore`, `.gitattributes`, `.gitmodules`, `.editorconfig`, and release metadata.
Exclude README, LICENSE, AGENTS.md, docs, scripts, tests, package files, and the manifest itself.
Exclude `.example` files. Examples explain setup; they are not installed settings.
Explicit paths cannot include repository furniture.

For Stow layouts, remove the `packages/<package>/` prefix. Stow is a tool that links home-shaped package directories.
Reject conflicting package destinations. No package can silently replace another package's output.
Map dots to chezmoi's `dot_` names. Preserve Git executable bits and symbolic links, which point to other paths.
Escape names that resemble chezmoi attributes. Preserve empty files.
See [chezmoi attributes](https://www.chezmoi.io/reference/source-state-attributes/) for the naming rules.
Reject absolute destination paths and parent traversal. Git determines file content and modes; ambient permissions do not define them.

## Manifest

`fleet.toml` is optional for plain content. TOML is a configuration file format.
The old name `dotfleet.toml` remains supported. It never combines with or overrides an existing `fleet.toml`.

```toml
format = "plain"
paths = ["agents/skills"]
overlays = ["core", "personal", "work", "client"]

[domains]
core = "Shared settings, without extra domain commands."
personal = "Settings for your own use."
work = "Settings for employer work."
client = "Settings for customer work."

[groups.docker]
description = "Docker container shortcuts"
blocks = [".config/dotfiles/aliases/core.zsh"]
files = []

[secrets.EXAMPLE_API_TOKEN]
reference = "op://Example/Development/api-token"
enabled = false
```

`paths` adds non-dot content paths. `overlays` declares domains; core means shared settings only.
The optional `domains` table gives one plain description per domain. A name in either place declares that domain.
Content creates and removes domains. Without `overlays` or `domains`, the content declares core, personal, work, and client.
Domain names use lowercase letters, digits, and single hyphens.
Core always applies, even when the content omits it. Each machine carries one domain.
A saved domain that the content no longer declares stops the next update before any write. Choose a declared domain to continue.
The wizard combines these descriptions with inspected overlay commands.
Empty overlay files add nothing; descriptions must not promise absent behavior.
Each group declares a description and optional `files` or `blocks` paths.
`files` selects whole files or directory trees. Disabled groups omit those outputs.
A block starts with `# dotfleet:group NAME begin` and ends with `# dotfleet:group NAME end`.
Markers occupy complete lines. Each declared block must appear exactly once per file. Disabled groups omit the entire marked block.
All groups default to on. Later runs keep choices; new groups prompt with on preselected.

## Agents table

An agent is an installed coding assistant, such as Claude Code or Codex.
The optional `agents` table in `fleet.toml` declares agent configuration in a neutral format. A neutral format names no agent's file layout.
Fleet validates the whole table before it plans any write. One failure stops the run.
Content without the table declares nothing for agents.

| Table or key | Holds |
| --- | --- |
| `agents.schema` | The integer 1. Fleet rejects a number that it does not know. |
| `agents.skills.NAME` | Domain limits for one skill folder. |
| `agents.rules.NAME` | One rule set: a tracked Markdown file of agent instructions. |
| `agents.mcp.NAME` | One MCP server. MCP means Model Context Protocol; a server offers tools to an agent. |
| `agents.connectors.NAME` | One hosted service that an agent reaches after the user signs in. |
| `agents.plugins.NAME` | One plugin, with one source per agent. |
| `agents.hooks.NAME` | One command that an agent runs when a named event occurs. |

Item names use lowercase letters, digits, and single hyphens. A name is unique within its table.
Unknown keys and unknown tables fail validation. A new key needs a new schema number.
Provider entries, such as OpenRouter or API-key providers, are not agents. The table has no kind for them.

Rule sets, servers, connectors, plugins, and hooks require `description`, one plain sentence.
They accept `agents`, a list of agent names, and `domains`, a list of declared domains.
Without `agents`, every selected agent that supports the item receives it. Without `domains`, every machine receives it.
An unknown agent name prints a warning and is skipped. Content can name an agent that an older manager lacks.
A domain must be one that the content declares.

Skills are ordinary content. Each folder under `.agents/skills/` that holds `SKILL.md` is one skill.
chezmoi installs skills at `~/.agents/skills`, as it installs other content.
A skill entry accepts `domains` only. It needs a tracked `.agents/skills/NAME/SKILL.md`.
A machine omits a skill when its domain is not listed. Plain content only; native chezmoi content installs every skill.

A rule set's `file` and a hook's `command` name files that Git tracks, relative to the repository root.
Absolute paths and parent traversal fail. A hook command must be executable in Git, mode 100755.

An MCP server's `transport` is `stdio` or `http`. `stdio` starts a local program; `http` calls a web address.

| Key | Transport | Meaning |
| --- | --- | --- |
| `command` | stdio, required | The program to start. |
| `args` | stdio | A list of arguments. |
| `env` | stdio | Variable names with plain, non-secret values. |
| `pass_env` | stdio | Names of variables that the server reads from the user's environment. |
| `url` | http, required | The server address. |
| `headers` | http | Header names with plain, non-secret values. |
| `header_env` | http | Header names mapped to variable names. |
| `bearer_token_env` | http | The variable that holds a bearer token, a secret string that proves access. |

The table holds variable names, never secret values. The `secrets` table can supply those variables through `~/.secrets`.
Fleet scans `env` and `headers` values with the adoption scan's secret patterns. A match fails validation.
A key from the other transport fails validation.

A connector accepts an optional `url` that starts with `https://`. The name `fleet` is reserved for the gateway.
A plugin's `sources` maps an agent name to an `id` and an optional `marketplace`. Fleet treats both as that agent's own notation.
Fleet installs and removes declared plugins through each agent's own installer. See [agent configuration](AGENTS.md#plugins).
A hook's `event` is `session-start`, `session-end`, `prompt-submit`, `before-tool`, `after-tool`, or `stop`.
Tool events accept `tools`: `shell`, `edit`, `read`, or `mcp`. `timeout` counts whole seconds.
Hooks and stdio servers run commands from content. Use only trusted repositories.

## Manager-owned outputs

Fleet reserves `~/.config/git/local.gitconfig`, `~/.config/dotfiles/domain`, `~/.config/mise/conf.d/fleet.toml`, and `~/.secrets`.
Plain content can provide Mise defaults in `.config/mise/config.toml`. Fleet merges them under profile tool versions into its fragment.
Content never manages `~/.config/mise/config.toml`. That file belongs to the user, and `mise use -g` writes there.
Native chezmoi content must leave reserved paths unmanaged. Other native outputs still use the original checkout.
Fleet also owns `~/.config/chezmoi/chezmoi.toml`. Content config templates never write that file.
After writing it, Fleet updates chezmoi's saved template checksum, an identifier for template contents.
Without a template, it removes the obsolete checksum. It preserves file history and script history.

Fleet owns the agent tree, `~/.fleet/agents`, and each agent pointer that it records. See [agent configuration](AGENTS.md).
Content cannot manage an agent pointer or a settings file where Fleet writes owned entries. That overlap stops the run before any write.

The local Git file holds profile identity and retains other existing local settings.
The domain file contains one domain name. Shell code reads it at startup.
Profile tool versions win over content defaults. A default applies only when its requested tool version already exists in Mise.
Uninstalled defaults appear as available choices. Enrollment never installs those tools.

A secret reference names a stored value without containing it. Enabled `op://` references use the signed-in 1Password command-line tool.
Secret variables must use valid shell names. Generated values are shell-quoted and use owner-only mode `0600`.
With no enabled references, preserve an existing `.secrets` exactly or create a comment-only placeholder.
Secret values never enter Git or the profile. Content declares references and names, never credentials.

## Ownership and updates

Back up every target before replacement. Preserve symbolic links and copy external file targets separately.
Save profile data at `~/.fleet/profile.toml`. Saved selections win over later defaults.
During legacy adoption, merge old chezmoi tool versions, current Mise tools, and saved installer tool versions, in that order.
Carry existing Mise settings into `mise_settings` too. This preserves settings beyond tool versions.
Validate carried and existing profiles before applying files. Ask about missing identity, unconfirmed domains, and floating tool versions.
Store full tool versions, resolving floating requests against installed versions only. Never install a tool to resolve a profile tool version.
Record explicit domain choices in `domain_chosen`. Carried and defaulted domains remain unchosen until a human confirms them.
Record skipped tools in `unpinned_tools`; content defaults must not select them.
Warn about choices skipped in a terminal. Without a terminal, stop and list every missing identity value, unconfirmed domain, and floating tool version.
Accept explicit `--domain`, `--git-name`, `--git-email`, and repeated `--tool TOOL=VERSION` values before validation.
The `--defaults` option cannot bypass incomplete values. Print new group defaults during scripted setup.
Complete saved choices remain authoritative. Carry identity and domain from saved data too.

The manager records output hashes, identifiers used to compare contents. Remove deselected outputs only when they still match their recorded state.
Stop on edited deselected outputs. Never delete unrelated files.
A second run must preserve selections and managed contents. New backups and chezmoi database changes are expected.
Report nonempty chezmoi status as incomplete enrollment. Keep all changes recoverable from backups.

## Enrollment review and adoption

Terminal enrollment stages content, conversion, and generated files outside home. Review lists choices, warnings, full changed paths, and the proposed backup path.
Store migration runs first; see [the migration rules](../README.md#store-migration). Cancelling the review moves those stores back.
Confirm before applying managed files. Returning to a step rebuilds the plan; quitting discards those planned changes.
Scripted flags keep their existing behavior. `--defaults` cannot bypass incomplete identity, domain confirmation, or floating tool versions.

Adoption scans root dotfiles and `.config` files without following directory links. File links are scanned through their targets.
Exclude credential and shell-history filenames, private keys, recognized tokens, and secret assignments. Exclude binary files and files above 2 MiB.
Report excluded paths without printing their contents. The scan is conservative but cannot guarantee secret detection.
Only accepted files enter the local repository. Review its contents before adding a remote and pushing.
An adopted manifest records `adopted = true`. Its authored files keep their exact bytes, including otherwise reserved generated paths.
Manager outputs fill absent paths; they do not rewrite adopted content. This exception preserves the adoption promise.
Agent selections are stored under `agents`. Generation remains optional and refuses unmanaged or modified outputs.
Back up agent outputs and their inventory before generation. Keep outputs for deselected agents; report them for manual review.

## Run accounts

Capture the chezmoi diff and backup inventory before apply. Use those records for both review and summary.
The review uses future tense. The summary reports confirmed results in past tense, including failures and scripted runs.
Print software, checkout changes, file changes, profile changes, locations, and next steps every time. Empty categories say no changes.
Print home-relative paths for additions, changes, and removals. Keep full paths in RUN.json. Include deselected outputs and settings changed by removed tool selections.
Preserve installed prerequisite versions and unchanged checkout commits in the report.

Save RUN.json and CHEZMOI-DIFF.patch beside the backup MANIFEST.json, with owner-only permissions.
RUN.json records planned actions and completion checks. The diff stays private and never appears in terminal reports.
Check recorded file operations after apply; do not rebuild their categories or discover new paths afterward.
Keep the last confirmed manager commit in ~/.fleet/last-run.json. Back it up before replacing it.
Internal state records, staged source copies, and backup copies are not rendered setting changes.
