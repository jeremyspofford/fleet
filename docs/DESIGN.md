# Layer 1 decisions

Fleet is Layer 1: the manager for machine enrollment and later changes.
Dotfiles is Layer 0: independent configuration content. Content uses real home filenames and does not depend on Fleet.
Git transfers repositories. chezmoi applies managed files. Mise manages tool versions.

These five decisions are settled. The term v1 names the first product scope, not permission to release version 1.0.0.

## 1. Use a wizard and flags

The wizard guides interactive enrollment. Flags supply the same choices in scripted runs.
A profile stores the selected content, domain, identity, exact tool versions, groups, and agents.
A domain selects optional content. Groups select related files or command shortcuts; agents are coding assistants.
Missing choices must remain visible. Without a terminal, incomplete required choices stop the run instead of accepting silent defaults.
Review planned changes before interactive apply. Read decisions from the terminal, never piped script input.

## 2. Keep the registry in the manager store

The registry is `~/.fleet/registry.toml`. A registry holds saved machine records; TOML is its structured file format.
Schema 1 uses a `machines` table keyed by fleet name. The `hostname` field equals that key.
A fleet name is the name saved in the machine's `~/.fleet/machine-name` at enrollment. An older enrollment saves the hostname it reported.
The `hostname` field holds that fleet name. The optional `system_hostname` and `distro` fields hold the OS hostname and the WSL distro as facts.
Those two facts stay local: an exchange never carries them. A machine records them when it meets a sibling through `wsl.exe`.
A member drops an unknown field from a received record instead of rejecting the record.
When two machine IDs report one name, the second key is `NAME~ID`, with the first 8 ID digits.
A sibling WSL distro's `ssh_target` is `wsl:DISTRO`. Fleet reaches it through `wsl.exe -d DISTRO`, never SSH.
Each record stores hostname, operating system, domain, content repository and commit, manager version, last run, and last backup.
A commit identifies a saved Git version. The registry retains full commit identifiers and absolute backup paths.
The `schema_version` field is 1. Each machine table contains these fields:

- Strings: `hostname`, `home`, `os`, `domain`, `content_repo`, `content_commit`, `manager_version`, `last_run`, `last_backup`, `last_run_record`, and `result`.
- Booleans: `domain_chosen` and `enrolled`. These record explicit domain choice and whether the machine retains an installed profile.

`ssh_target` is an optional string: an SSH configuration host name or `user@host`. Missing or empty means `no connection`.
`machine_id` is an optional string of 32 hexadecimal digits. Each machine generates it once in `~/.fleet/machine-id`; it identifies the record.
`aliases` is an optional list of earlier fleet names. Records from before IDs that use an alias merge into the member.
`recorded_at` is an optional string: when the facts were recorded.
Registration defaults the name to its SSH target. Either --name or --target supplies both, unless both flags differ.
The machines set-ssh command saves under the same registry lock and backup rules as removal. It then tests SSH once.
Failure keeps the saved link and returns exit 2 with guidance.
New connection records may have no machine facts yet. Record refreshes preserve the saved connection.

`last_run` uses UTC time in ISO format. UTC is the common time reference; ISO format stores an unambiguous date and time.
Paths remain absolute. `home` records the machine's home path so reports can print its saved paths with `~`.
The registry does not store identity, tools, or credentials.
Use unique hostnames when combining records manually.

Example record; the values below describe an illustrative machine:

```toml
schema_version = 1

[machines."example-hub"]
hostname = "example-hub"
home = "/home/example"
os = "Linux"
domain = "personal"
domain_chosen = true
content_repo = "https://github.com/jeremyspofford/dotfiles"
content_commit = "0123456789abcdef0123456789abcdef01234567"
manager_version = "0.2.0"
last_run = "2026-11-16T12:00:00+00:00"
last_backup = "/home/example/.fleet/backups/20261116-070000"
last_run_record = "/home/example/.fleet/backups/20261116-070000/RUN.json"
result = "SUCCESS"
enrolled = true
```

Backups include the registry for manual recovery. Automatic rollback never replaces it, and uninstall never removes it.
These operations preserve other machines' records. Each operation refreshes this machine's record; uninstall records `enrolled = false`.
Inspect current records before any manual recovery. An older registry can contain stale facts about other machines.

Backed-up apply, update, restore, and backup runs refresh this machine's record, including failures.
Read-only commands and cancellations do not update timestamps. Cancelling before confirmation leaves managed files unchanged. It also moves this run's migrated stores back.
`dotfleet machines list` prints saved records already present in the file. It does not contact other machines or prove that their records are current.
Keep profile data and the registry out of the content repository. Machine synchronization and a live fleet view remain deferred.

## 3. Keep status local in v1

Without a machine name, `dotfleet status` reports this machine only. It shows profile choices, checkout state, drift, and recent runs and backups.
A checkout is a local Git copy. Drift means managed files differ from the selected content.
The behind count compares content with the last fetched remote state. Plain status never fetches.
It adds one line from the saved update check, with its time. Run check separately to refresh it.
`dotfleet doctor` checks local readiness and names fixes. Missing domain provenance counts as unchosen until a user selects a domain.
Provenance records how a choice was made. Passing a domain explicitly records a human choice.

### Share update selection

`dotfleet check` and update share upstream selection and comparison. An upstream is the remote branch tracked by a checkout's current branch.
They compare both manager and content. Update preserves these upstream settings when replacing its manager checkout.
Both refuse diverged histories. Diverged histories contain new local and remote commits that cannot combine by moving forward.
Both refuse uncommitted manager edits and uncommitted edits to managed content files.
Unmanaged content files never block. An update that would change one with local edits stops before backup.
Local commits stay intact when the remote adds nothing. Adopted content without a remote stays local.

Check quietly fetches each source with a ten-second limit.
Update first refreshes that comparison and reads local drift. Current sources without drift stop before backup or review. A small run-index entry records the attempt.
A failed check stops without applying files. Behind sources or drift proceed through the existing installer path.
Update records manager update, content update, drift repair, and profile change as its triggers. Both review views use those recorded reasons.
The terminal interface and remote targets call this same update core. Installer enrollment and repair keep their existing behavior.
Neither substitutes the remote's default branch for the selected upstream. New commits after a check can change the next update's result.

The manager owns `~/.fleet/check.json`. Content cannot manage this file.
Schema 1 stores `checked_at` and `sources`. Each source records its name, path, branch, remote, current and upstream commits, target commit, and ahead/behind counts.
The time includes its time zone. The cache keeps full paths and commit identifiers; command output shortens commits.
Check replaces the private cache atomically only after both sources succeed. A failed check leaves the previous cache unchanged.
It creates no backup or run record and writes no profile or managed files.
Exit codes are 0 for current, 1 for updates, and 2 for an unavailable check.
Plain status reads only the cache. Without a valid cache, it says “not checked yet.”

## 4. Let Fleet own releases

Fleet owns the release workflow, version file, changelog, policy script, and tests.
Its release machinery follows the former dotfiles setup: GitHub Actions and release-please.
GitHub Actions runs checks. Release-please proposes version changes from Conventional Commits, commit messages with defined type prefixes.
The content repository does not own manager releases.

Stay in the zero-major version range until the owner explicitly declares production readiness.
Automation must never propose version 1.0.0. That version requires a deliberate manual release by the owner.
The owner reviews and merges release pull requests. See [the release rules](RELEASES.md).

## 5. Keep Bash and Python

Bash provides the installer and thin command entry. Python handles profiles, conversion, run records, and validation.
Use the existing manager path for changes. One renderer prints plans and completed run records.
The renderer shows readable paths and commit identifiers; machine records retain full values.

The terminal interface uses Textual in `~/.fleet/tui`, a manager-owned virtual environment with separate Python packages.
The installer and update maintain it after review and backup. Exact requirements define its contents; Software records each change.
The plain command core uses only the standard library. Missing interface packages never prevent plain commands.

Interface actions run the existing command dispatcher in a separate process. This isolates terminal input, environment variables, and Python output streams.
A private inherited socket carries reviews and answers. A socket is a local communication channel; it has no listening network address.
Only the shared core validates confirmation words and applies files. Both views render the same run record.
Menus cover every command and group action. Forms send named choices to the shared dispatcher; they do not implement changes.
Parity tests compare reachable menus with the parser. Golden files check each primary output shape.
A golden file stores expected output. Machine lists use separate blocks, omit empty fields, and exclude run-record paths.
Status and dashboard never fetch. Updates uses the shared check and update cores.
Machines offers the same remote command core only for records with an SSH target.
Configure closes Textual before the original wizard takes the terminal. Unattended conflicts stop without choosing overwrite.
Keep Git, chezmoi, and Mise as the proven tools. Do not add a custom sync engine or background synchronization daemon.

Add-ons follow the [add-on contract](ADDON-INTERFACE.md). None ship with this version.
The contract defines declarations and ownership before an executable add-on interface is introduced.

## On-demand remote operations

SSH runs the target's installed command. The target owns execution, profile validation, backups, rendering, and run records.
The local manager validates status, check, diff, doctor, or update before starting SSH. Both ends validate the request.
A fixed remote shell command starts the receiver. User arguments never enter that command.
A versioned JSON request travels through standard input. JSON is a structured transfer format; it is never printed to the terminal.
Version 1 requests contain one action and a boolean option for diff paths. The old status-check field must remain false.
Version 2 requests carry one profile choice: domain, groups, agents, or tools, with named values that both ends validate.
No request can name a profile file, path, executable, or another target.
A profile request saves the target's profile only. The target's manager is its only writer.
The update gate compares the profile with the one that the last apply recorded. A difference triggers a profile change update.

Before dispatch, a fixed probe checks for the installed command. Missing commands and remote exit 127 receive the same installation guidance.
Each SSH call uses batch mode, no terminal, one connection attempt, and a ten-second connection timeout.
Keepalive checks stop a dead connection. Live updates have no artificial time limit.
The target disables terminal decisions and Git password prompts. Update passes --defaults to the existing installer.
Incomplete profiles fail before action dispatch. Unattended file conflicts fail without overwriting local edits.
Configure, rollback, and uninstall remain local-only. There is no persistent network listener, remote file editing, or cross-machine profile transfer.
The wizard stays local. Remote profile choices travel as validated values, never as files.

The target's shared renderer supplies human output. The caller adds the machine name and result, without recording a local install run.
Target shell errors never reach the terminal interface or CLI. CLI means the plain command interface.
Private diagnostic RUN.json files under ~/.fleet/remote-runs preserve raw output and failures.
Check and diff preserve their meaningful exit 1 results. SSH failures return nonzero with one diagnostic line and one next step.
An interrupted connection leaves the update outcome unknown; the owner must inspect the target's last run.

## SSH discovery and recorded topology

The terminal picker and TUI read the same concrete Host names from the user’s SSH config and Include files.
Relative Include paths start at ~/.ssh, matching OpenSSH. Includes expand file patterns in sorted order; visited files prevent include cycles.
Wildcard and negated Host patterns, duplicate names, and registered names or targets are excluded.
The collector does not execute Match commands. SSH itself resolves the chosen host's connection settings.
No available names means typed input. No terminal means explicit flags are required.

Registration runs the fixed SSH command true, with a ten-second total timeout. Remote operations still use the target's own manager.
Fleet never configures networks, relays, SSH keys, or SSH settings. Missing access produces guidance, not an automatic setup attempt.

The local contact cache is ~/.fleet/contacts.json. Schema 1 records target, action, outcome, exit code, and observation time per saved name.
Writes are private, atomic, and locked. Cache failures warn without changing the remote operation's outcome.
Topology reads this cache and the local check cache. It never fetches or copies another machine's store.
A changed target cannot inherit an old target's observation. Missing observations say never contacted.
Every saved result has its time and a recorded label. The header names the viewing machine; views are not synchronized.
Only the explicit topology --json option prints structured output. The normal CLI and TUI share the same tree renderer.

## Product name and stores

The command remains `dotfleet`; the guarded `fleet` alias avoids an existing Rancher Fleet command.
The content manifest is `fleet.toml`; the older `dotfleet.toml` name remains readable. The new name wins when both exist.
The machine registry is `~/.fleet/registry.toml`.

Enrollment and update move old stores into `~/.fleet` before other work. The [migration rules](../README.md#store-migration) list every move.
Profiles and backup payloads stay intact. Rollback maps their old paths during restoration and repairs chezmoi's source pointer.
Migration appears under Profile changes in the run record and review. Cancelling the run undoes those moves.
Internal module names, environment variables, and `.fleet` records keep their existing names.

## Backup cleanup

Backups list shows dates, results, and sizes. Delete accepts one selector: --name, --keep, or --all.
The shared planner records selected paths, dates, file sizes, and directory contents before confirmation.
The CLI and terminal interface show the same review. Both require typing delete before removing anything.
The terminal interface uses checkboxes for selected backups and also offers Delete all.
A changed backup cancels deletion before any folder is removed. Deletion never follows symlinks to their targets.

Cleanup records live at `~/.fleet/cleanup-runs/<run>/RUN.json` with owner-only permissions.
They record completed removals and refresh this machine’s registry entry. Other machine records stay intact.
The cleanup store sits outside .fleet/backups, so deleting all backups leaves that store empty.
Deleted backups cannot be restored. Cancellation creates no cleanup record and removes nothing.

## Keep bounded run attempts

The run index records each change-making attempt before profile validation or apply. It keeps the newest 100 JSON entries.
JSON is a structured format that scripts can read. Each entry records progress and links to RUN.json when available.
The installer and nested update share one entry. Remote updates keep that entry on their target.
Read-only commands never create entries. Queries can refresh their existing fixed caches.
Cancellation preserves managed files and keeps a small cancellation entry. It moves this run's migrated stores back.
The Logs screen and commands read the same index. Applied attempts use the existing review renderer.
See [RUNS.md](RUNS.md) for storage, cleanup, and diagnostic limits.

## Deploy agent configuration through one tree

The agents table in content declares agent configuration. The catalog in the manager names native paths. The profile saves this machine's choices.
Fleet renders one payload per machine into `~/.fleet/agents`, a Fleet-owned tree.
Updates change the tree. Each agent gets one pointer to it, set once. See [agent configuration](AGENTS.md).
Agents that accept no pointer receive owned entries in their shared settings files.

The foundation is the tracked content that Fleet installs. Updates and diffs apply only there.
Skills are ordinary content at `~/.agents/skills`. Whole instruction files stay ordinary content.
The inventory at `~/.fleet/agents.json` lists every owned path and entry. Schema 2 replaced the earlier flat record.
Fleet compares only keys that it wrote. A conflict stops the run before backup; unattended runs never choose overwrite.
Stale outputs that still match their record leave. Changed stale outputs stay, with one report line. Turning an agent off keeps its outputs.

## Pull in packages with Fleet Marketplace

Fleet Marketplace pulls agent packages from marketplaces into the agent tree. See [Fleet Marketplace](MARKETPLACES.md).
A marketplace is a Git repository with a `fleet-marketplace.toml` catalog.
Pulled-in components live in an untracked folder, `~/.fleet/marketplace-packages`. The installation record lives in the manager store.
No pulled-in file enters the user's tracked content. Updates never rewrite the user's own files.
A refresh replaces a pulled-in component, keeps any edited copy in the run backup, and prints one line.

## Serve MCP servers through one gateway

The Fleet gateway is a stdio MCP server in the manager. Each agent receives one entry for it.
The gateway serves every declared downstream server's tools, prompts, and read-only resources.
Served is a render kind beside the file kinds. Connectors, hooks, plugins, settings, and skill folders stay on files.
The gateway reads its downstream list from the tree. Updates change that list, never the agent's entry.

## Deploy Fleet to another machine

`fleet machines install NAME` takes a machine from no record to fleet member in one command at the caller's terminal.
It probes over SSH, checks the floor, bootstraps Python when needed, and runs the installer with a terminal on the target.
The floor is bash, curl, git, tar, gzip, and a CA certificate bundle. The Python bootstrap also needs sha256sum or shasum.
Fleet installs no system packages and uses no admin rights. Fleet's own Python lives at `~/.fleet/python`.

Every remote command is fixed. Staged files and version 3 requests carry all values through standard input.
Enrollment counts only when the target's own record says enrolled with a successful result. A process exit alone proves nothing.
Both registries gain each other's record only after enrollment succeeds. A failed reverse connection test is reported only.
The machines list holds enrolled members. Connection records from set-ssh stay data for remote commands and the picker.
FLEET_MACHINE_NAME overrides a machine's fleet name. Tests and containers use it to run two machines on one host.

## Converge fleet lists on contact

Every remote command exchanges a registry digest after it runs. The digest holds member records and removal markers.
Each member record is complete except its SSH target. Members match by machine ID; the hostname is a display name.
The answer names the machine that answered: its ID and hostname. The caller binds that ID to the record it contacted.
A newer record with a new hostname renames its member in place. The saved SSH target stays with the renamed member.
A machine's own record carries its installed manager version. Publishing a manager and every contact recheck it.
The union of members wins. The newest record time wins each field. A machine is the only authority on its own record.
A removal marker beats any member fact recorded at or before it. A newer deploy clears an older marker.
A digest sends no SSH targets. A merge never changes a connection that the receiver saved.
A removal marker names the machine ID. Markers from older members name a hostname and still apply.
Records from before IDs merge when one is the machine's own record and both share a last run time and content commit.
Without that evidence, both records stay, and the list names the possible duplicate.
Contact merges write the registry under its lock without a backup, as the contact cache does. Read-only commands stay read-only in the run index.
`machines uninstall NAME` confirms on the caller, then sends a version 3 unenroll. The member removes Fleet with backups kept and no restore.
