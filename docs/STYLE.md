# Code style

Use [WRITING.md](WRITING.md) for headers, help, comments, and documentation.

1. Start every script with its purpose and usage.
2. Give Bash scripts a `usage()` function. Connect it to `--help` and `-h`.
3. Use argparse for Python help. Argparse reads command arguments and prints help. Give each command a useful description.
4. Format shell scripts with shfmt and tabs. Format Python with Ruff, the repository’s Python formatter, at 100 columns.
5. Separate logical blocks with blank lines. Split long statements. Keep one action per line.
6. Explain reasons in comments. Do not repeat obvious code. Remove stale comments and commented-out code.
7. Keep functions small. Give each function one job. Put the main flow after its helpers.
8. End Python files with a main guard. This condition runs entry code only when the file is executed directly.
9. Use names that explain their purpose. Avoid abbreviations that a new reader must decode.

Use Prettier, the JavaScript formatter, for release helpers.

## Bash versions

Fleet's shell needs bash 4.4 or newer. macOS ships bash 3.2, so a bootstrap layer comes first.
The bootstrap layer finds a modern bash, or installs one with Homebrew after the user agrees.

1. Keep the bootstrap layer safe for POSIX sh and bash 3.2. It runs before any upgrade exists.
2. The bootstrap layer is `scripts/bash_floor.sh`, the bash check at the top of `install.sh`, the deploy probe and bash install scripts, and `scripts/dotfleet.sh`.
3. In that layer, use no arrays, no `mapfile`, and no `[[ ]]`. Never expand an empty array under `set -u`.
4. Expand all arguments as `"$@"`, which bash 3.2 allows under `set -u`. Expand a saved array as `${name[@]+"${name[@]}"}`.
5. The launcher, `scripts/dotfleet.sh`, stays wholly in that layer. It must start on bash 3.2 to reach `fleet update`.
6. All other shell code may use modern bash. The bootstrap guarantees 4.4 or newer before that code runs.
7. Start scripts with the resolved bash by path. Python reads it from `FLEET_BASH`; never rely on PATH order over SSH.

The `test_bash_floor` tests run the bootstrap layer on a real bash 3.2 in the `bash:3.2` container image.

## Command parameters

Use flags for optional parameters and for values that a reader can confuse with each other.
Commands and action words stay positional. A positional argument is a value without a named flag.
The single trailing machine in status, check, diff, doctor, and update is the subject. Keep that machine name positional.
Tools set and remove take one tool name as their subject. Keep the version as a named flag.
Tools update takes an optional tool name as its subject. Without it, the action covers every selected tool.
Use --name and --target for SSH registration. One flag supplies both values unless the other flag gives a different value.
Help and examples show flags. Keep promised positional SSH-registration forms readable, but do not document them as the main interface.
Command groups require explicit action verbs. A bare group prints its action list.
Sort command names, action names, and flags alphabetically in help. Sort flags by their long name, ignoring leading hyphens.
Use raw-text help formatting. Preserve authored line breaks; put each example on its own line.
Help never shows exit codes. Record them in a source comment instead, such as the script header.
Top-level help ends after global options. Put command-specific details in that command’s help, and define shared terms in the README.
Support help, -h, and --help at each command and action. Support help COMMAND and both version and --version.
Status reads saved state without network requests. Check fetches updates. Keep their help and output distinct.

The command audit uses these forms:

| Command | Parameters |
| --- | --- |
| machines install | NAME; --target, --return-target, --domain, --git-name, --git-email, --repo, --tool, --update-bash; no NAME opens the picker |
| machines uninstall | NAME |
| machines set-ssh | --name, --target; no flags opens the terminal picker |
| machines remove | --name |
| backups delete | Exactly one of --all, --keep, or --name |
| machines export | --output |
| domain list / set | set requires --name; --machine selects a registered machine |
| marketplaces add | --source; optional --features, --name, --packages, --pin |
| marketplaces delete | --name |
| marketplaces update | Optional --name; with --name, optional --pin or --unpin, and --all, --packages, or --features |
| marketplaces list | No value parameters |
| agents import | --agent |
| agents list / set | set requires --name and --state; --machine selects a registered machine |
| groups list / set | set requires --name and --state; --machine selects a registered machine |
| tools set | TOOL, --version; --machine selects a registered machine |
| tools remove | TOOL; --machine selects a registered machine |
| tools update | Optional TOOL; --machine selects a registered machine |
| tools uninstall | TOOL, --version |
| backups restore | --backup; no flag lists backups |
| status, check, diff, doctor, update | One optional machine subject; diff also has --stat |
| topology | --json selects structured output |
| content | No value parameters |
| capture | One or more FILE paths |
| dotfiles own | No value parameters |
| dotfiles merge-base | No value parameters |
| dotfiles source | --repo; --machine selects a registered machine |
| tools list | --machine selects a registered machine |
| machines list, backups list, secrets list, addons list | No value parameters |
| logs list | --limit |
| logs show | --json, --name |
| backups create, tui, configure, version, uninstall | No value parameters |
| Installer and agent generator | Existing named flags |
| Converter | --checkout, --output, --profile, --format |

Every command and group action needs a reachable terminal-interface entry and a primary-output golden test.
Use the shared core for actions, reviews, records, and confirmations. List each entity in its own block; omit fields without values.
Internal bridges also label optional values, such as --backup and --machine. Function arguments are not command-line parameters.

## Preserve behavior

Run the same tests after a style change. Keep output, exit codes, and golden files unchanged.
Golden files store expected output. Add missing help without changing other behavior.
Record any file that needs a behavior change before it can meet these rules.

Imported helper modules may provide help only. Keep their existing behavior when called without help.
Sourced shell configuration is not a command. Do not add argument parsing that changes the caller’s shell.

Argument errors use the shared parser handler. Show the failing command’s usage, then name the problem and accepted arguments. Exit 2.
