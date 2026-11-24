# Fleet

Renamed from Dotfleet, November 2026.

The installed command is `dotfleet`. Users can type the `fleet` alias.
Rancher's Fleet already provides a `fleet` command. We keep `dotfleet` to avoid PATH wars.
PATH lists the folders your shell searches for commands. The alias stays undefined when a real `fleet` command already exists.

Fleet manages home configuration across machines. A profile saves each machine's repository, domain, Git identity, tool versions, and content groups.
Git records history. chezmoi applies files. Mise selects developer tool versions. A tool selection records a version; a group selects optional content.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/fleet/main/install.sh | bash
```

- Setup backs up existing files and keeps your editable content in `~/.fleet/dotfiles/NAME`.
- Re-run the same command to update. Saved selections remain unchanged.
- Backups live in `~/.fleet/backups/<timestamp>/`. Review `MANIFEST.json` before restoring `home/` files and external targets.

Requires bash 4.4+, git, curl, tar, and Python 3.11+. Use Linux, macOS, or WSL (Windows Subsystem for Linux).
Missing chezmoi and Mise install into `~/.local/bin`. Setup never uses sudo.

macOS ships bash 3.2, which is too old for Fleet's scripts. Fleet then offers to install bash with Homebrew:

1. Setup states the bash version that it found, before anything is installed.
2. Type `yes` to install bash with Homebrew, or `stop` to quit. Press Enter after either word.
3. After the install, Fleet runs every script in that run with the new bash, named by its full path.

Without a terminal, setup stops unless you pass `--update-bash`, the scripted form of `yes`.
Without Homebrew, setup stops and prints the Homebrew install command. Fleet never installs Homebrew, because its installer needs admin rights.
A machine that already has bash 4.4 or newer sees no prompt. Fleet looks on PATH and in the standard Homebrew folders.
The wizard runs in a terminal for a new profile, incomplete choices, an unchosen domain, or `--configure`.
Prompts read the terminal, never piped input. Complete profiles update without enrollment questions.

1. Read the welcome screen. Backups come before replacement; review comes before apply.
2. Review the scan: operating system, installed Mise tools, home dotfiles, and existing enrollment.
3. Choose a source. **adopt** keeps this machine's files; **example** uses our example files; **repo** accepts your Git URL.
4. Choose a domain. The prompt lists the domains that your content declares, with descriptions and overlay commands.
5. Confirm Git name and email. Enter keeps the displayed value; type `skip` explicitly to leave it empty.
6. Compare installed tools, content defaults, and saved tools. Accept them or enter exact versions.
7. Choose groups. On includes their content; off excludes it. New groups say NEW.
8. Select detected agents, which are coding assistants. The step first counts the agent configuration your content declares, or says it declares none. Set each detected agent on or off. Press Enter to keep its current choice.
9. Review all choices and the full planned change account. Press Enter to confirm, type `quit` to cancel, or type a step name to return.
10. Read the apply result and summary warnings.

**Start with `core` unless you want a domain's examples.** It includes shared shell, Git, aliases, and prompt settings.
The bundled `personal`, `work`, and `client` overlays each add two harmless example commands: show your directory and list its entries.
Replace those examples with your own settings. Empty overlays explicitly say they add nothing yet.
Choosing a domain installs no software and creates no accounts. Identity and tool versions are separate choices.
A new profile shows `core`; later prompts show your recorded domain. Confirm it by pressing Enter or entering another domain name.

Prompts use headings, spacing, and the shared logger's colors. Text labels keep choices clear without color.
Set `NO_COLOR` to disable colors, including an empty value. Redirected output stays plain.

### Adopt existing files

Adoption is recommended when the scan finds home dotfiles. It scans root dotfiles and `.config` files, excluding manager state and cache directories.
It excludes credential filenames, detected secrets, binary files, and files above 2 MiB. Logs, histories, caches, telemetry, session files, and receipts are not candidates.
The scan shows one line per category, such as Shell or Browser data. Each line has a file count and up to three examples.
RUN.json keeps the full scan and private exclusion list.
The scan cannot prove that remaining files contain no secrets. Review your local repository before pushing it.
Adopted files keep their exact contents, including existing Mise files. Profile choices do not rewrite adopted files.
Only accepted files enter the first local Git commit. Nothing is uploaded.

1. Review `~/.fleet/dotfiles/adopted` and its Git history.
2. Create an empty repository on your chosen Git host.
3. Run `cd ~/.fleet/dotfiles/adopted && git remote add origin URL`, replacing URL with that repository's address.
4. Run `git push -u origin main` when ready.

Each prompt lists its choices one per line: the word to type, then its effect.
Each prompt marks the recommended or current choice and states what Enter alone does.

Setup never reads or changes a `~/dotfiles` folder of your own. Content lives in `~/.fleet/dotfiles`.
Native chezmoi templates and hooks can execute code. Use trusted content only.
Quitting review cancels planned file changes. A small attempt entry records the cancellation. Fleet also moves this run's [store moves](README.md#store-migration) back.
Preparation uses temporary files outside home; missing tools move into `~/.local/bin` only after confirmation.

### Flags and updates

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/fleet/main/install.sh | bash -s -- --configure
```

`--configure` repeats the wizard with saved choices. `--defaults` uses complete recorded values without prompts; new groups default to on.
Without a terminal, incomplete profiles stop and list their gaps. No prompt or default silently fills identity, domain confirmation, or floating tool versions.

| Flag | Meaning |
| --- | --- |
| `--repo URL` | Use a content Git URL or local repository. |
| `--adopt` | Create a local repository from scanned home files. Cannot combine with `--repo`. |
| `--domain NAME` | Confirm a domain that your content declares. Content defines the additions. |
| `--git-name NAME` | Set the Git commit author name. |
| `--git-email EMAIL` | Set the Git commit author email. |
| `--tool TOOL=VERSION` | Record an exact version. Repeat for more tools. |
| `--group NAME=on\|off` | Include or exclude a named group. Repeat for more groups. |
| `--agents NAMES` | Configure detected agents, separated by commas. Use none to skip. |
| `--format plain\|chezmoi` | Choose real names or native sources when detection is unclear. The manifest takes priority. |

Read `--help` for examples and limits. Help never shows exit codes.

## Run account

Every run prints six categories, including scripted runs. Empty categories collapse to one line.
A clean no-change run has about twelve content lines, plus spacing between sections.
Its Locations line points to RUN.json; the record retains every location. Review screens and changed runs show locations in full.
The wizard uses the same run record before apply. It describes planned actions; the end summary describes confirmed results.
Paths use `~` for your home directory. Commit IDs identify saved Git versions and show eight characters.
Full paths and commit IDs remain in RUN.json. Present software shares one quiet line.
File markers mean added (+), removed (−), or changed (~). Color reinforces these markers when a terminal supports it.
Set NO_COLOR to disable color. Redirected output stays plain.

File lists include generated settings and agent files. Removing a tool selection does not uninstall software.
Backups, converted sources, and internal state are not counted as setting changes.
Manager updates compare the running commit with the last confirmed run. Missing earlier values say “not set.”

The backup contains MANIFEST.json, which lists original files, and RUN.json, which records planned actions and confirmed results.
CHEZMOI-DIFF.patch records content and permission differences before apply. These private records can contain secret values.
Run reviews show paths. `dotfleet diff` shows patches, which can contain secret values. Failed runs report confirmed changes only.
An interrupted run directs you to its backup record when completion is unknown.

To roll back manually:

1. Read the backup's MANIFEST.json.
2. Restore saved home/ files and recorded external targets. Remove only new paths marked absent.
3. Keep later user edits and the backup. Restore the profile before running setup again.

This example is checked against the renderer's recorded test fixture:

```text
1 · Software — no changes.
  Already present: chezmoi 2.74.1, Mise 2026.8.5, Fleet 0.2.0

2 · Manager and content — no changes.

3 · Files — 1 added, 1 changed, 1 removed.
  + ~/.zshrc
  ~ ~/.aliases
  − ~/.config/docker/aliases.zsh

4 · Profile changes — 1 changed.
  Group docker  on → off

5 · Locations
  Content  ~/.fleet/dotfiles/default
  Profile  ~/.fleet/profile.toml
  Source   ~/.fleet/chezmoi-source
  Backup   ~/.fleet/backups/20261116-140000

6 · Next steps
  Update    fleet update
  Rollback  ~/.fleet/backups/20261116-140000/MANIFEST.json
  Wizard    fleet configure

chezmoi status: clean · warnings: none
Run record: ~/.fleet/backups/20261116-140000/RUN.json
```

## How it works

[dotfiles](https://github.com/jeremyspofford/dotfiles) provides plain content with real home names. It works without Fleet.
The [content contract](docs/CONTENT-CONTRACT.md) defines files, formats, groups, secrets, and ownership.
Fleet converts plain files into a private chezmoi source. Native chezmoi repositories use their checkout directly.
Stow layouts under `packages/<package>/` also work; their package prefixes do not reach the home directory.

Your profile lives at `~/.fleet/profile.toml`. It saves the domain, Git identity, exact tool versions, and group choices between runs.
Use `fleet tools set TOOL --version VERSION` to select and install a version. Mise records which versions are installed; Fleet reads that list during setup.
Use `fleet tools update TOOL` to select and install the newest version that Mise finds. The profile still records that exact version.
Use `fleet tools adopt` to record the installed version of each tool without a recorded version. Adopt installs nothing.
A saved tool choice selects a version. It does not mean that version is installed.
Fleet writes the recorded versions to `~/.config/mise/conf.d/fleet.toml`. `~/.config/mise/config.toml` is yours, and `mise use -g` writes there.
Mise reads `config.toml` after `conf.d`, so your own entry for a tool overrides Fleet's.
An update from an older Fleet moves Fleet's pins out of `config.toml`. Your own entries stay, and the run prints one line.
Setup asks when identity is incomplete, the domain choice is unconfirmed, or a tool version floats, such as `latest`.
Prompts show recorded values. Press Enter to use the stated action, or type `skip` to leave a choice incomplete.
For identity, Enter keeps a nonempty displayed value. Type `skip` explicitly to leave identity empty.
Floating tool versions resolve to matching installed versions. Without a match, enter a full version, such as `24.14.1`, or leave the tool without a selection.
Exact versions remain unchanged. Complete profiles need no profile prompts.
The summary lists skipped gaps under Warnings. Without a terminal, incomplete identity, an unconfirmed domain, or a floating tool version stops setup. `--defaults` cannot bypass this check.
For scripted runs, supply missing choices explicitly:

```sh
bash install.sh --domain personal --git-name "Example Person" --git-email person@example.invalid --tool node=24.14.1
```

`--domain` records the domain choice. `--git-name` and `--git-email` record commit identity.
Repeat `--tool TOOL=VERSION` for each full version. A floating value in a flag still fails unattended validation.
Complete profiles need no flags on later runs. New group defaults are printed during scripted setup.

`domain_chosen` records a human choice in this wizard, through `--domain`, or through `dotfleet domain set --name NAME`.
A missing field means UNCHOSEN, including profiles saved before the wizard existed. Carried and defaulted domains remain unchosen until confirmed.
Status and doctor show that gap. Run `dotfleet domain list` to read the choices, then `dotfleet domain set --name NAME` to confirm one.
`unpinned_tools` records skipped tools, so content defaults cannot select them later. Remove a tool there to allow its defaults again.
Content defaults fill gaps only when the requested versions are installed. The summary lists other defaults as available, without installing them.
The manager creates Git identity, domain, Mise settings, and a private secrets file. Keep secret references in the content manifest, never values. Imported profile references remain valid.
A reference such as `op://Vault/Item/field` names a secret in 1Password. References are disabled unless explicitly enabled.

Old Layer 0 enrollment migrates automatically. Setup reads old chezmoi data, rendered Mise tools, and saved installer data before applying files.
Migration also saves existing Mise settings in the profile. Existing Fleet profiles remain authoritative. Backups preserve replaced files and prior staged sources.
Conflict menus explain letter keys and require Enter. Skipping changes can leave differences; the summary reports that as incomplete enrollment.

1. Edit content files in the active download under `~/.fleet/dotfiles`. Change profile choices through `dotfleet domain`, `tools`, `groups`, or `configure`.
2. Commit and push content changes before updating. Setup refuses uncommitted edits to managed files.
   Unmanaged files, such as untracked notes, never block setup.
3. Run `dotfleet update` to convert and apply. The one-liner remains available for manager recovery.
4. Run `dotfleet status` to check for differences.

To roll back, restore saved `home/` files and external targets from the backup. Remove only new paths marked absent.
Restore the saved profile too. Keep the backup and rerun setup when you want to regenerate files.

## Agents, add-ons, and releases

An agent is a coding assistant. The content's agents table declares skills, rules, servers, connectors, and hooks; see [agent configuration](docs/AGENTS.md).
Fleet deploys them through one Fleet-owned tree and one pointer per agent. It installs and starts no agent.
The [add-on contract](docs/ADDON-INTERFACE.md) remains a proposal. The enrollment wizard uses terminal prompts. The terminal interface uses the same command cores. No add-on runtime, custom sync engine, or daemon exists here.
`VERSION` starts at 0.1.0. Release automation stays in 0.x; only the owner can approve a manual 1.0.0 release.
Read [release rules](docs/RELEASES.md) and [writing rules](docs/WRITING.md).

## Checks

CI means automated change checks. It runs shell checks, formatting, release-policy tests, and container enrollment tests.
Container tests cover plain content, the private Stow archive, and a native chezmoi fixture.
They check adoption, secret exclusions, review cancellation, repeat runs, shell startup, group changes, saved profiles, and clean status.
The plain suite runs without Textual. A separate environment runs Textual pilot tests, which simulate keys and mouse clicks.
Pilot tests check the dashboard, updates, typed restore confirmation, narrow layouts, and plain themes.

For local checks, create a test environment with `requirements-tui.txt`. Set `FLEET_TEST_TUI_ENV` to an installed manager environment for reusable enrollment fixtures.
Run `python3 -m unittest discover -s tests -v` with standard Python.
Run that environment's Python with `-m unittest discover -s tests/tui -v` for screen tests.

## After enrollment

Use `dotfleet` for daily commands. The installer places it in `~/.local/bin` on every run.
It keeps the manager checkout at `~/.fleet/manager`. The command uses that copy and its VERSION file.
Keep `~/.local/bin` on PATH, the list of folders your shell searches for commands.
The one-line installer remains the first-install and recovery path.
Command groups require an action. Bare `machines`, `backups`, `secrets`, `tools`, `groups`, `domain`, and `addons` print their action lists.

| Command | Action |
| --- | --- |
| `dotfleet tui` | Open the full-screen terminal interface. Missing environments show plain command alternatives and exit 0. |
| `dotfleet update` | Check upstreams and drift first. Apply only when an update or repair is needed. |
| `dotfleet update --all` | Update this machine, then each enrolled member in turn. One failure never stops the rest; a summary names each result. |
| `dotfleet backups list` | List backup dates, results, and sizes without changing files. |
| `dotfleet backups delete --name BACKUP` | Review and delete one saved backup. |
| `dotfleet backups delete --keep N` | Review deletion of all but the newest N backups. |
| `dotfleet backups delete --all` | Review deletion of every saved backup. |
| `dotfleet secrets list` | List declared secret names as set or unset. Values stay private. |
| `dotfleet logs list --limit 10` | Show the newest ten change-making attempts. |
| `dotfleet logs show` | Read the latest review or failure. Use --name for a timestamp or backup. |
| `dotfleet content` | Show the content checkout, repository, commit, behind state, and short Git status. |
| `dotfleet status` | Show choices, installed tools, drift, recent runs and backups, and the last update and tool checks. |
| `dotfleet topology` | Show this machine’s recorded connection tree. Use --json for structured output. |
| `dotfleet check` | Fetch both upstreams and save their update counts. An upstream is the remote branch tracked by a checkout. Also report selected tools with a newer version. |
| `dotfleet diff` | Show pending patches. Use `--stat` for paths only. Exit 0 means clean; exit 1 means differences. |
| `dotfleet backups restore` | List backups and choose one in a terminal. Include the saved profile when restoring. |
| `dotfleet backups restore --backup BACKUP` | Review one backup folder, then type restore to confirm. |
| `dotfleet domain list` | Show the current domain, its confirmation state, and each domain's description. |
| `dotfleet domain set --name personal` | Record the personal domain as chosen and apply it. |
| `dotfleet groups list` | List content groups, their states, and descriptions. |
| `dotfleet groups set --name docker --state off` | Exclude Docker content. Use on to restore it. |
| `dotfleet marketplaces add --source URL` | Install every package from a marketplace of agent packages. See [Fleet Marketplace](docs/MARKETPLACES.md). |
| `dotfleet agents set --name mcp.docs --state off` | Remove one agent item on this machine. Use local to keep this machine's own value, or on to restore it. |
| `dotfleet tools list` | List saved exact versions and installed versions. |
| `dotfleet tools set node --version 24.14.1` | Explicitly install and record that exact version. Content defaults still never install software. |
| `dotfleet tools uninstall node --version 26.3.1` | Uninstall an extra installed version through Mise, after typing uninstall to confirm. |
| `dotfleet tools remove node` | Remove the selection and prevent content defaults from filling it again. Keep installed software. |
| `dotfleet tools update node` | Ask Mise for the newest version, then record and install it as tools set does. A current tool stays unchanged. |
| `dotfleet tools update` | Update every selected tool. Print one result line per tool, then a summary. Exit 1 when any tool failed. |
| `dotfleet tools adopt` | Record the installed version of every unrecorded tool. Print each skipped tool with its reason. |
| `dotfleet tools adopt jq` | Record one tool. With several versions, adopt the one Mise reports as active. With none active, skip it. |
| `dotfleet configure` | Reopen the enrollment wizard. |
| `dotfleet doctor` | Check profile completeness, drift, checkout edits, and backups. Print a fix for each failed check. |
| `dotfleet machines list` | List saved machines with known facts and connections. Make no network requests. |
| `dotfleet addons list` | List installed add-on records. No add-ons ship with Fleet. |
| `dotfleet backups create` | Snapshot managed files and the profile without applying content. |
| `dotfleet version` | Print the manager version and content commit. |
| `dotfleet uninstall` | Review removal, then type uninstall. Keep the content checkout and all backups. |

Each command group also answers to its other number. For example, `machine` works exactly as `machines`.
The pairs cover addons, agents, backups, domain, groups, logs, machines, marketplaces, secrets, and tools. Help shows one name.

Older command forms now print their successor and exit with code 2:

| Previous form | Current form |
| --- | --- |
| `rollback` | `backups restore` |
| `content show` | `content` |
| `profile show` | `status` |
| Previous tool-selection group | `tools list`, `tools set TOOL --version VERSION`, or `tools remove TOOL` |

Tool names are positional subjects. A subject names the thing a command acts on; optional values remain flags.
Status includes saved agents and the profile location. Its update result stays inside the same field block.
Help remains available as `help`, `-h`, and `--help`; it is a global operation, not a separate command-list entry.

A checkout is a local Git copy. Plain status reports saved update results and never fetches.
Its Updates line includes the check time, or says not checked yet.
Its content behind count compares the last fetched remote state. Check prints only update results.
Drift means that managed files differ from the selected content. Groups select optional files or alias blocks.
The profile stores machine choices. Tools use exact selected versions.
A domain selects content additions. A commit identifies a saved Git version.
Use `help`, `-h`, or `--help` at any command or action. `fleet help machines` lists machine actions.
Top-level help lists commands and global options. Each command’s help keeps its own examples. Help never shows exit codes.
Use `version` or `--version` for the same version output. Exit codes are 0 for success or cancellation, 1 for failed checks or operations, and 2 for arguments.
Check uses 0 for current, 1 for available updates, and 2 when a check fails.

An unchanged update refreshes the check cache and stops without a backup or review. Its small index entry records NO_CHANGE.
Other change-making commands write RUN.json and print the standard review. File lists come from the plan and backup inventory.
Rollback makes another backup first. It restores only recorded paths, including original symlinks and recorded external targets.
It refuses directory replacement and changed external targets whose original symlink is missing.
It verifies saved file fingerprints before writing. A fingerprint records the file's content hash and permissions, or its symlink target.
A content checkout remains at its current commit after rollback. Use diff to review differences before updating again.
Uninstall offers to restore the oldest backup. Otherwise, it removes known managed files and the manager's command, checkout, converted source, and terminal interface environment.
Uninstall keeps installed tools, the content checkout, and all backups. Removal includes local edits, which the new backup preserves.
Rollback and uninstall require terminal confirmation. A scripted rollback without a folder only lists backups.

Backup deletion accepts exactly one selector: --name, --keep, or --all. A selector chooses which backups to remove.
Use --keep 0 to remove all backups; negative counts are invalid.
Review every selected backup’s date and size, plus the total size. Type delete and press Enter to proceed; cancel keeps everything.
Deleted backups cannot be rolled back to. Content and the current managed files stay intact.
Deletion writes its review to `~/.fleet/cleanup-runs/<run>/RUN.json`, outside the backup store.
This keeps --all from creating a replacement backup. Dates identify backups; sizes count file and link bytes without following links.

## Terminal interface

Run `fleet tui` after enrollment. Use `dotfleet tui` when the guarded alias is unavailable.
A terminal user interface (TUI) fills the terminal with navigable screens.
The dashboard fits 80 columns and 24 rows. Narrow terminals keep one column; long dashboard values shorten with an ellipsis.
Status shows the full values. Scroll the other screens to read long results.

The dashboard shows this machine, domain choice, content source and commit, manager version, cached updates, last backup, and drift.
Each menu area opens the existing command's view or action:

| Area | Commands and actions |
| --- | --- |
| Machine | Status, Check, Update, Diff, Doctor, and Topology. Diff also offers paths only. |
| Configuration | Status contains saved choices. Domain list/set; Groups list/set; Tools list/set/remove/update; Secrets list; Content; Configure. |
| Machines | List, pick an SSH host, set a named target, export, and remove a stale record. |
| Backups | List, create, restore, or delete backups. Checkboxes select backups for deletion. |
| System | Version, Add-ons list, command help, interface help, and Uninstall. |

Connected machines offer Status, Check, Diff, Doctor, and Update from Machines and Topology.
Unconnected machines remain display-only. Help includes every command and action; interface help describes the already-open TUI.
Configure closes the interface and runs the original terminal wizard. No commands are excluded from the interface.
CI checks every command and group action against reachable menu entries.

Arrows and Tab move between choices. Enter selects. Esc goes back or cancels review. Press q to quit.
During a change, q waits for that operation to finish. Review can still be cancelled before applying.
Mouse selection also works. Set `NO_COLOR`, even empty, or `TERM=dumb` for the plain theme.

The interface calls the existing command cores. Changes show the same review and write the same RUN.json records.
Forms collect named choices; the command core validates them. Cancel leaves those choices unapplied.
Type `confirm` for an update, `restore` for rollback, or `uninstall` for removal. Empty or incorrect answers never approve changes.
Uninstall offers to restore the oldest backup, as the plain command does. After removal, close the interface.
Remote Update runs unattended on the selected machine. It shows that machine's completed review; it never asks for target input.
Incomplete profiles stop updates; choose Configure to finish them. Local file conflicts also stop unattended apply.
Use the plain update command in a terminal to resolve those conflicts. No interface action guesses an overwrite choice.

The installer and update create `~/.fleet/tui`, a private Python environment owned by the manager.
A virtual environment holds separate Python packages. [Textual](https://textual.textualize.io/) supplies the terminal widgets.
`requirements-tui.txt` selects exact versions of Textual and its dependencies. The Software category records environment creation or updates.
An unchanged environment stays untouched. A replaced environment moves into that run's backup as `previous-tui-venv`.
Failed environment installation restores the previous environment. A missing environment leaves the complete plain command interface available.
Run `fleet update`, or the README installer, to restore it. No system packages or sudo are used.
The command core needs only Python's standard library. Textual loads only in the interface process.

## Where Fleet keeps its state

Fleet keeps its own state in one folder, `~/.fleet`:

| Path | Holds |
| --- | --- |
| `~/.fleet/manager/` | The manager checkout that runs Fleet. |
| `~/.fleet/profile.toml` | This machine's saved choices. |
| `~/.fleet/registry.toml` | Recorded facts about fleet members. |
| `~/.fleet/backups/` | Backups, one folder per run. |
| `~/.fleet/runs/` | The run index, which lists recent change-making attempts. |
| `~/.fleet/cleanup-runs/` | Records of backup deletions. |
| `~/.fleet/remote-runs/` | Private diagnostics from remote commands. |
| `~/.fleet/check.json` | The last update check. |
| `~/.fleet/tools-check.json` | The last check for newer tool versions. |
| `~/.fleet/contacts.json` | Recorded connections to other machines. |
| `~/.fleet/chezmoi-source/` | The converted source that chezmoi applies. |
| `~/.fleet/agents/` | The agent deployment tree. |
| `~/.fleet/agents.json` | The inventory of agent outputs that Fleet wrote. |
| `~/.fleet/marketplaces.json` | Saved Fleet Marketplace records. |
| `~/.fleet/marketplaces-check.json` | The last marketplace check. |
| `~/.fleet/marketplace-packages/` | Installed marketplace packages. |
| `~/.fleet/addons/` | Add-on records. |
| `~/.fleet/outputs.json` | Fingerprints of managed files from the last apply. |
| `~/.fleet/last-run.json` | A short record of the last apply. |
| `~/.fleet/tui/` | The private Python environment for the terminal interface. |
| `~/.fleet/python` and `~/.fleet/mise/` | Fleet's own Python, when a deploy installed one. |
| `~/.fleet/deploy/` | Files that a deploy staged on this machine. |
| `~/.fleet/dotfiles/NAME/` | Downloaded dotfiles repositories, one Git clone each. The profile names the active one. |

Some files stay where other tools read them. The command stays at `~/.local/bin/dotfleet`.
Generated files such as `~/.secrets` and the chezmoi and Mise configs also stay where those tools read them.

## Dotfiles repositories

Fleet keeps each dotfiles repository you download in `~/.fleet/dotfiles/NAME`. One of them is active.
Update, check, diff, and the wizard use only the active repository. They never touch the others.

1. Run `fleet dotfiles download URL` to clone a repository into the library. Downloading changes no applied file.
2. Run `fleet dotfiles list` to see each download, its source, and the active one.
3. Run `fleet dotfiles use NAME` to activate a download. Review the changes, then type `use` and press Enter.
4. Run `fleet dotfiles remove NAME` to delete a download. You cannot remove the active one.

Names:
- **Default:** the name comes from the repository name. The starter pack is named `default`, and adopted files go in `adopted`.
- **Your own:** `--name NAME` chooses the name up front. A name uses only letters, numbers, dashes, and underscores.
- **Taken names:** if a name is taken, a terminal asks for another one. Without a terminal, the download stops and names the collision.
- **No silent changes:** Fleet never renames a download silently or overwrites one.

Using a repository takes a backup and applies its files.
Files that only the previous repository managed are removed, unless you edited them. Fleet keeps edited files and reports each one.
Domain, groups, tools, and agents carry over. If the new repository does not declare your domain, Fleet applies core and says so.
Your saved domain stays, so switching back applies it again.

## Capture an edit

To capture means to copy an edit of a managed home file back into content.
Every machine then receives it at its next update.
This first version takes whole files from plain and Stow content.

1. Edit the home file, such as `~/.zshrc`.
2. Run `fleet capture ~/.zshrc`. You can name several files.
3. Review the change to each content file.
4. Type `capture` and press Enter to commit. Press Enter alone to cancel; nothing changes.

After the commit, Fleet pushes to `origin` only when you can push there.
Otherwise the commit stays in your checkout, and Fleet says so.
`fleet status` then names each content commit that is not pushed, with the next step.
Fleet then runs the normal update for this machine, so no drift remains.

Capture refuses a file in these cases, and changes nothing for it:
- **Secret:** an added line looks like a secret. Use a `[secrets]` reference instead.
- **Group marker:** the edit changes a `# dotfleet:group` line.
- **Conflict:** newer content changed the same lines.
- **Not a text file:** a credential file name, a link, binary data, or more than 2 MiB.

Blocks of groups that this machine turns off stay in content.
For native chezmoi content, run `chezmoi re-add FILE` instead.
The terminal interface offers Capture under Configuration. It asks for the file in the terminal.

## Store migration

The installer and installed command move older stores into `~/.fleet` before other work:

- `~/.local/share/fleet/` and `~/.local/share/dotfleet/` move into `~/.fleet/`.
- `~/.config/fleet/` and `~/.config/dotfleet/` move into `~/.fleet/`.
- `~/.local/state/dotfleet/` moves into `~/.fleet/`.
- `~/fleet-backups/` and `~/dotfleet-backups/` move to `~/.fleet/backups/`.
- `~/.cache/fleet-deploy/` moves to `~/.fleet/deploy/`.
- A `~/dotfiles` checkout of the saved content moves to `~/.fleet/dotfiles/NAME` and stays active. An unrelated `~/dotfiles` stays.

Each item moves intact. Files keep their bytes, and old backups come along. Emptied old folders are removed.
The registry becomes `~/.fleet/registry.toml`, and the terminal interface environment becomes `~/.fleet/tui/`.
The content manifest is `fleet.toml` in the content checkout. Older `dotfleet.toml` manifests still work; `fleet.toml` wins when both exist.

Fleet updates chezmoi's source pointer and links inside `~/.fleet` during migration. Rollback translates old backup paths to the new locations.
A second run finds nothing to move and changes nothing.
The review lists each store move under Profile changes. Cancelling the run moves everything back to its old place.
If a name already exists in `~/.fleet`, migration stops before moving anything and reports one line. Fleet never merges two copies silently.

## Remove an extra tool version

1. Run `fleet tools list`. A recorded tool's row names extra versions after "also".
   Unrecorded tools appear under "Also installed — not recorded".
2. Run `fleet tools uninstall node --version 26.3.1` with the extra tool and version you want to remove.
3. Review the version and path. Type uninstall and press Enter to confirm, or cancel to stop.

Fleet refuses the recorded version. Use `fleet tools remove node` to clear that choice, or select another with `fleet tools set`.
Tools remove never uninstalls software. Tools uninstall keeps the profile unchanged and saves a run review.
Its backup copies managed files, not installed software. Reinstall software with tools set when needed.

## Run history

1. Run `fleet logs list --limit 10` to list recent change-making attempts, newest first.
2. Run `fleet logs show` to read the latest attempt.
3. Use `fleet logs show --name TIMESTAMP` to select a listed timestamp. A backup folder name also works.
4. Use `fleet logs show --json` to export the index entry and its saved review for scripts.

An index is a small list of records. Fleet keeps its newest 100 entries in `~/.fleet/runs/`.
Each attempt records its time, command, machine, result, stage, error, and backup when present.
Installation, configuration, updates, and other change-making actions create entries. Target-side remote updates write the target's entry.
An update with no work records `NO_CHANGE`, without a backup or review. Cancellation records `CANCELLED`, without applying files.
Read-only commands, including status, diff, check, and logs, create no entries.
The terminal interface has the same list and details under Logs.

Applied runs show their saved review. Earlier failures show the stage reached and error line.
Deleting a backup removes its RUN.json. The small index entry remains until it falls outside the newest 100.
Cleanup and remote diagnostics also keep their newest 100 records. No text log grows or needs rotation.
See [run record storage](docs/RUNS.md) for all retained data and limits.

## Check for updates

1. Run `fleet check` to fetch manager and content changes without applying them.
2. Read `fleet status` for the last result and its check time.
3. Run `fleet update` to fetch and apply changes. Run check again to refresh the saved result.

The `fleet` alias runs `dotfleet`. Use `dotfleet` directly if your content does not provide that alias.
Status never fetches. Run check first when you need fresh update counts.
Each fetch stops after ten seconds. Network or repository failures print a warning and return exit code 2.
A failed check preserves the previous result and its time.

Check and update use each checkout's configured upstream, including a branch other than the remote's default.
Both preserve local commits when the remote has no new commits. Both refuse missing upstreams and histories with conflicting new commits.
Both refuse uncommitted edits in the manager checkout and uncommitted edits to managed content files.
A managed content file is a tracked file that Fleet installs, or the manifest. Other content files are unmanaged.
Unmanaged content files never block. Both stop when an update would change an unmanaged file with local edits.
Locally adopted content without a remote has no updates to fetch.
Update fetches again, so commits published after a check can also arrive.
It also checks local drift before creating any backup. A failed fetch or drift check stops the update without applying files.
When both sources are current and no drift exists, update prints:

```text
Fleet is up to date — manager and content match upstream, checked just now. No backup taken.
```

Otherwise, the review names the trigger: manager update, content update, drift repair, profile change, or their combination.
A profile change is a saved choice that no update has applied yet, such as a remote domain assignment.
Existing conflict rules still apply. Local edits require a terminal decision; unattended conflicts stop safely.
The same gate applies to terminal-interface and remote updates. The one-line installer still handles enrollment and repair.

Successful checks save both results in `~/.fleet/check.json` with owner-only permissions.
This cache is saved check data. It contains the check time, full commit identifiers, and source paths.
Check creates no backup or run record and never writes the profile or managed files.
Status shows “not checked yet” until a valid result exists. Plain status keeps working offline.

## Recorded machines

The fleet registry records machines in `~/.fleet/registry.toml`, inside the manager store. It is not content and never enters your dotfiles checkout.
Run `dotfleet machines list` to read this machine's record and any other records in that file.
Each machine has its own block. The list hides empty fields and keeps run-record and backup paths in their dedicated commands.
A connection-only record says no local data is recorded. These are saved facts, not live results. Named remote commands query one machine on demand. Automatic registry transfer remains deferred.

Each completed apply, update, restore, or backup refreshes this machine's record. Other machine records stay intact.
The record contains machine ID, hostname, operating system, domain, domain confirmation, content repository and commit, manager version, last run, and last backup.
It also records the home folder, RUN.json path, result, and whether this machine remains enrolled. Full commit IDs and paths stay in the file.
The machine ID identifies the record. Each machine generates its ID once, at enrollment, in `~/.fleet/machine-id`.
A machine's fleet name is chosen on the machine at enrollment and saved in `~/.fleet/machine-name`. Fleet never takes it from the SSH config.
An enrollment from before saved names keeps the hostname it reported. Its first run or check saves that name.
Fleet never changes a saved name.
Every first enrollment asks for the name before its other steps. Press Enter to accept the proposal, or type another name.
The proposal joins the host and the system. Linux: `dell-fedora`. macOS: `dell-macos`. WSL: `dell-ubuntu-26.04-alertventure`, with the distro as scope.
A name that names this distro with another version, such as `dell-ubuntu-24.04` on 26.04, is asked again.
`--machine-name NAME` supplies the name for a scripted enrollment. Without a terminal and without it, a first enrollment stops before any write.
The record also keeps the hostname and, in WSL, the distro as displayed facts.

`~/.fleet/install-stages.json` records each completed install stage: detect, software, enrollment, and verify.
Run the same command again after a stop. Fleet skips a completed detect stage, reuses its name, and says so in one line.
A first enrollment ends with `fleet check` and a section that lists what ran automatically, what was skipped, and what is left to do.
A failed check leaves the enrollment complete. The section names the failure and the command that repeats it.
Members match by machine ID. When a member reports a new name, the next contact renames its record in place.
Two machines that report one hostname both list. The second lists as `HOSTNAME~ID`, and the list notes the collision.
Records from before IDs merge when one is this machine's own record and both share a last run time and content commit.
Without that evidence, both records stay, and the list names the possible duplicate.
Identity, tool versions, credentials, and secret values stay out of the registry. They remain in their existing private stores.
Read-only commands and cancellation before confirmation never refresh timestamps or create registry files.
Uninstall records the completed removal without deleting other machine records. Backups include the earlier registry file for manual recovery.
Automatic rollback does not restore that file. It refreshes this machine instead, preserving other machines recorded since the backup.

The schema, meaning the file's required structure, starts at version 1. The format is defined in [the decision record](docs/DESIGN.md).
The registry is private, with file mode 0600. Invalid registry data stops setup before apply.
Content cannot manage the registry or profile file. These belong to the manager.

## Add-on records

An add-on is an optional manager extension. Run `dotfleet addons list` to list validated installed records. No add-ons ship with Fleet.
The [add-on contract](docs/ADDON-INTERFACE.md) defines installation declarations, allowed data access, output ownership, and safe teardown.
A test fixture validates the contract. It is not installed or offered as a product add-on.

Use `dotfleet machines export` to view this machine’s saved record alone.
Use `dotfleet machines export --output FILE` to save a private TOML copy for transfer. FILE must not exist.
TOML is the configuration format used by the registry. Export never prints its raw contents.
Use `dotfleet machines remove --name HOSTNAME` to remove another machine’s stale record. Fleet backs up the registry and records the change.
The active machine’s record cannot be removed this way.

## Remote commands

A machine joins the fleet from another machine. Run one command at your own terminal:

```sh
fleet machines install mini-pc
```

NAME is a host from your SSH config, or any name with `--target user@host`. NAME only reaches the target.
The target's own saved name becomes its fleet name in every list. No registration step comes first.
Run `fleet machines install` without NAME to pick from your SSH config hosts and saved connections. The picker needs a terminal.
SSH is the secure connection tool. The deploy uses your SSH config and keys, in batch mode, without password prompts.

The deploy runs these steps in order:

1. Probe. Connect once and detect Linux, macOS, or WSL. Check the floor: bash 4.4+, curl, git, tar, gzip, and a CA certificate bundle.
2. Update bash. When the target's bash is older than 4.4, ask in your terminal, then install bash with Homebrew.
3. Bootstrap. When Python 3.11 or newer is missing, install Mise and Python 3.12 in the target user's own folders.
4. Enroll. Run the Fleet installer on the target with a terminal. Its wizard prompts appear in your terminal.
5. Register both ways. Your registry gains the target under the hostname it reports, with your SSH target for it. The target's registry gains your machine and the SSH target that reaches it.
6. Prove the loop. Run a remote status against the target and print its result after your own run review.

A missing floor item stops the deploy. The report names each item and the one command that installs it.

Fleet installs no system packages and uses no admin rights. The bootstrap also needs sha256sum or shasum.
A CA certificate bundle lets curl verify HTTPS; the probe reports `ca-certificates` when verification fails.
A network failure during the probe is not a floor item. Later steps report it.
The probe reports the bash version, not only its presence. Homebrew often sits outside PATH over SSH, so the probe checks the standard Homebrew folders too.
The bash prompt names the version found and the Homebrew command. Type `yes` to install, or `stop` to end the deploy with nothing installed.
Without a terminal, the deploy stops before installing unless you pass `--update-bash`. Without Homebrew, the report prints the Homebrew install command.
The target then runs the deploy script and the installer with that bash, named by its full path, never found through PATH.
The Python bootstrap keeps its own Mise store at `~/.fleet/mise`, apart from your tool versions.
The installer reads the target's facts: its scan, wizard steps, and review describe the target.

### Sibling WSL distros

Two WSL distros on one Windows host are two machines. They share one hostname, so their saved names tell them apart.
A sibling is another distro on this host whose own Fleet answers with a record. Fleet reaches it through `wsl.exe`, never SSH.

```sh
fleet machines install AlertVenture --machine-name dell-wsl-alertventure
```

1. NAME is a distro from `wsl.exe --list`. Fleet runs the deploy steps through `wsl.exe -d AlertVenture`.
2. Use `--target wsl:DISTRO` when NAME is the new machine's name and differs from the distro.
   A bare `--target` keeps its SSH meaning. It names a distro only with `--via wsl`.
3. A scripted deploy needs `--machine-name`. With a terminal, the target's wizard asks for the name.
4. Both registries record the other with a wsl connection, such as `Connection  wsl:AlertVenture`.

A name that matches a distro and an SSH config host stops. So does a name with a saved SSH target.
Add `--via wsl` or `--via ssh`; Fleet never guesses.
`update`, `check`, `tools`, `machines uninstall`, and `update --all` reach a sibling through `wsl.exe`. A stopped distro starts.
The result line names the sibling dispatch. A first enrollment in WSL registers every enrolled sibling both ways.
A distro without Fleet never enters the registry.
Without Windows interop, enrollment still succeeds. One line says siblings were not checked and names the step to enable interop.
Fleet never creates, deletes, or configures a distro. It never edits `/etc/wsl.conf`, `.wslconfig`, or your SSH config.

A scripted deploy passes `--domain`, `--git-name`, and `--git-email`, and optionally `--repo` and repeated `--tool TOOL=VERSION`.
Without a terminal and without those three flags, the deploy stops before connecting and names the missing flags.

`--return-target` names the SSH target that the new machine uses to reach yours. Without it, one prompt asks; skip saves no connection.
The deploy tests that reverse connection once and reports the result. A failed reverse test never blocks the enrollment.

| Failure | Exit | Effect |
| --- | --- | --- |
| Unreachable | 2 | SSH failed. The report names the reason and one next step. |
| Missing floor | 2 | The report names each missing item and its install command. |
| Bash declined | 1 | You typed stop at the bash prompt. Nothing was installed. |
| Wizard aborted | 1 | The target's installer stopped before enrollment completed. |
| Enrollment failed | 1 | The installer or the Python bootstrap failed. |
| Reverse test failed | 0 | Report only. The enrollment and both registrations stand. |

A failed deploy registers nothing on either machine. Deploy is a change-making action: both machines record it in their run index.

The machines list holds fleet members only. A machine that was never deployed to does not appear in it.
`fleet machines set-ssh --target HOST` saves connection data for remote commands and the deploy picker. Connection data stays out of the list.
Set-ssh tests SSH once with the fixed command `true`, with a ten-second timeout. A failed test exits 2 and keeps the saved connection.

Run a command on a fleet member. The final argument is its fleet name, or the SSH target that reaches it:

```sh
fleet status mini-pc
fleet check mini-pc
fleet diff mini-pc
fleet diff --stat mini-pc
fleet doctor mini-pc
fleet update mini-pc
```

Each remote command, including install, also exchanges a compact registry digest with the target.
The digest holds each member's complete record, except its SSH target, plus removal markers.
A record includes the machine ID, name, domain and its confirmation, content and commit, last result and time, version, OS, and enrollment.
A machine rechecks its own manager version whenever its manager is republished, and on each contact.
Both machines merge it: the union of members wins, and the newest record wins each field. Lists converge when machines talk.
A removal marker beats an older member fact, so a removed machine never returns from an old peer list.
A machine stays the only authority on its own record. No background service runs.
Members match by machine ID, never by name. A removal marker names the machine ID.
How to reach a machine is each viewer's own fact. A digest sends no SSH targets, and a merge never changes a saved one.

Remove Fleet from another member with `fleet machines uninstall mini-pc`. Type `uninstall` at your terminal to confirm, then press Enter.
The member removes its manager and managed files under the uninstall rules. Its content, backups, and own files stay.
Your machine then drops the member and writes a removal marker. Peers drop it at their next contact.
`fleet machines remove --name NAME` stays local record cleanup. It also writes a removal marker.

Only saved machines can receive commands. An unknown name never starts SSH.
Targets use their own profiles, tools, repositories, and backups. Your machine sends an action, never a profile or file paths.
Omit the machine name to keep a command local. There is no all-machines command or background service.

### No SSH setup yet?

A reachable `user@host` or `user@IP` target needs no SSH config file. The target must run an SSH server and accept your key.

1. Check the SSH client. OpenSSH ships with macOS and most Linux systems; install your Linux distribution's client package if missing.
2. Run `ssh-keygen` if you need a key. Copy only the public key with `ssh-copy-id user@host`, where that tool is available.
3. Confirm normal SSH access and the host key. Then register the target with Fleet.

On Windows clients, enable the OpenSSH Client optional feature.
On Windows targets, add the public key to the user's `.ssh/authorized_keys` file.
Administrator accounts use `%ProgramData%\ssh\administrators_authorized_keys` instead. Restrict that file's permissions to Administrators and SYSTEM.
See Microsoft's [Windows key instructions](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).
Tailscale is one option for making machines reachable; it is not required.
Fleet never installs or configures a VPN, Tailscale, or a relay. A VPN is a private network connection.
Fleet reads SSH configuration but never edits it. It uses the SSH client, keys, and network access you already have.

A missing client, unresolved target, failed authentication, or missing Fleet command prints an error and one next step.
Remote commands probe for the installed command before dispatch. Each SSH call uses one attempt and a ten-second connection timeout.
Dead connections stop through SSH keepalive checks. An absent command or exit 127 asks you to run the installer on that target.
SSH keeps your host checks and never forwards your authentication agent or extra network channels.

Target runs never prompt. Incomplete profiles name the missing choices and stop; file conflicts stop without choosing overwrite.
Configure requires a terminal on the target. Remote rollback and uninstall are refused because they are destructive.
Run those commands on the target itself.

Profile choices also run on a registered machine. Add `--machine NAME` to `domain`, `groups`, `agents`, or `tools` list and set actions, and to `tools remove`, `tools update`, and `tools adopt`:

```sh
fleet domain set --name work --machine mini-pc
fleet update mini-pc
```

1. The target validates the choice against its own content and saves its own profile, with a backup and run record.
2. The target's next update applies the saved choice. Its review names the reason: profile change.

The target's manager is the only writer of its profile. `tools set` also installs that exact version on the target through Mise.
`tools update` asks the target's Mise for the newest version, installs it there, and saves it. The target's next update applies it.
If one tool fails there, the target still saves the other tools and exits 1.
`tools adopt` reads the target's installed versions and saves them there. The target's next update applies them.
An invalid or undeclared value fails on the target with exit 2; nothing is saved.

The target renders results under its machine header. Updates that change files write RUN.json in its backup folder, using the local run renderer.
The caller creates no backup or install run record for remote execution. It saves the contact's outcome and time locally.
Raw target errors stay out of both interfaces. The newest 100 private diagnostic records live under `~/.fleet/remote-runs/<run>/RUN.json`.
Check refreshes the target's update cache. Exit 0 means success; check and diff retain exit 1 for updates and differences.
Other failures return nonzero. A lost connection cannot prove whether an update finished; inspect the target before retrying.

### View connections from this machine

Run `fleet topology` for a text tree. Run `fleet topology --json` for the same facts as JSON, a structured format for scripts.
The TUI Topology screen shows the same tree. Select a connected machine for its remote actions; unconnected records are display-only.
These are recorded observations with times, never claims of live state. Viewing the tree makes no network requests.
The local check cache supplies this machine's update state. Contact observations supply other machines' last-known results.
Unknown domains say not recorded. New targets say never contacted, including a target changed since its last observation.
Fleet keeps no shared machine state. Another machine's tree can differ.

Example from the test fixture:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Topology from example-dell
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Recorded observations only; no live checks. Other machines may have different views.

example-dell · domain personal
  updates current · recorded 2026-11-17T14:05:00+00:00
├── mini-pc · SSH uses this name · domain personal
│   status succeeded · recorded 2026-11-17T14:00:00+00:00
├── retired · no connection · domain client
│   never contacted
└── workstation · SSH user@lab · domain work
    never contacted
```

Contact observations live in `~/.fleet/contacts.json`, with private file permissions.
Topology is the human view of that cache. No profile data enters the content repository.

Use `dotfleet status` to inspect saved choices. Change them through `domain`, `tools`, `groups`, or `configure`; do not edit profile.toml.
The manager’s chezmoi configuration and converted source are internal files. Inspect their locations through `status` and their health through `doctor`.

## Terminal output

Python commands share the colors and section rule from `scripts/logger.sh`. Shell entry points use that logger directly.
Colors reinforce labels and change markers. Redirected output, `NO_COLOR`, and `TERM=dumb` disable colors.
Errors use the diagnostic output stream and return a failure code. Unexpected errors never print a traceback, Python’s technical call history.
Applied runs keep technical error detail in private RUN.json. Earlier failures keep their stage and error in the run index.
