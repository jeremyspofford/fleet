#!/usr/bin/env bash
# Purpose: Install Fleet and enroll or update a content checkout.
# Usage: bash install.sh [OPTIONS]
# Parameters: --repo chooses content; --configure asks again; --defaults skips prompts for complete profiles.
# --domain, --git-name, and --git-email supply profile choices. Repeat --tool TOOL=VERSION for full tool versions.
# --adopt keeps scanned home files. --group NAME=on|off selects groups. --agents chooses coding assistants.
# --format selects plain (real names) or chezmoi (native sources); -h/--help prints help.
# --update-bash accepts installing bash with Homebrew when the found bash is older than 4.4.
# Example: bash install.sh --repo https://github.com/jeremyspofford/dotfiles
# Exit codes: 0 success/help; 1 setup failure; 2 invalid arguments.
# Without a terminal, missing identity, an unconfirmed domain, or floating tool versions exit 1.
# Author: Jeremy Spofford / Aria Labs; Version: read from VERSION.
set -euo pipefail
raw=https://raw.githubusercontent.com/jeremyspofford/fleet/main
manager_repo=https://github.com/jeremyspofford/fleet.git
script_dir=''
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
	script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fi
work_dir=$(mktemp -d)
export DOTFLEET_SUMMARY_MARKER="$work_dir/summary-reported"
export DOTFLEET_APPLY_MARKER="$work_dir/apply-started"
summary_expected=1
index_script=''
run_stage=preflight
run_error=''
run_command=install
for argument in "$@"; do
	case $argument in
	-h | --help) summary_expected=0 ;;
	--configure) run_command=configure ;;
	esac
done
# Keep a minimal attempt even when Python or the downloaded logger cannot start.
fallback_attempt() {
	local store="$HOME/.fleet" identifier stamp machine error path old
	# Before store migration, attempts stay in an older store so migration moves them along.
	for old in .local/share/dotfleet .local/share/fleet; do
		if [[ -d $HOME/$old ]]; then store="$HOME/$old"; fi
	done
	[[ ! -L $store && ! -L $store/runs ]] || return 1
	local prefix
	for prefix in .local .local/share .local/share/fleet .local/share/dotfleet .fleet; do
		[[ ! -L $HOME/$prefix ]] || return 1
	done
	umask 077
	mkdir -p "$store/runs" || return 1
	chmod 700 "$store/runs"
	path=$(cd -- "$store/runs" && pwd -P)
	[[ $path == "$(cd -- "$HOME" && pwd -P)"/* ]] || return 1
	stamp=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
	identifier="$(date -u +%Y%m%d-%H%M%S)-000000-$(printf '%04x%04x%04x' "$RANDOM" "$RANDOM" "$RANDOM")"
	machine=${HOSTNAME:-unknown}
	error=${run_error:-"Setup stopped during $run_stage (exit $1)."}
	# Bash escaping keeps this fallback independent of Python, Git, and curl.
	json_string() {
		local value=$1
		value=${value//\\/\\\\}
		value=${value//\"/\\\"}
		value=${value//$'\n'/ }
		value=${value//$'\r'/ }
		value=${value//$'\t'/ }
		printf '"%s"' "$value"
	}
	local temporary="$path/.$identifier"
	(
		umask 077
		printf '{"schema_version":1,"id":"%s","time":"%s","command":"%s","machine":' "$identifier" "$stamp" "$run_command"
		json_string "$machine"
		printf ',"remote":%s,"result":"FAILED","stage":' "$([[ ${FLEET_REMOTE_RUN:-} == 1 ]] && printf true || printf false)"
		json_string "$run_stage"
		printf ',"error":'
		json_string "${error:0:2000}"
		printf ',"backup":null,"record_path":null}\n'
	) >"$temporary"
	chmod 600 "$temporary"
	mv -- "$temporary" "$path/$identifier.json"
	local entries=("$path"/????????-??????-??????-????????????.json) index
	for ((index = 0; index < ${#entries[@]} - 100; index++)); do rm -f -- "${entries[index]}"; done
}
finish_run() {
	local result=$?
	trap - EXIT
	if [[ $summary_expected == 1 && -z ${FLEET_RUN_ID:-} && $result != 0 ]]; then
		fallback_attempt "$result" || printf 'Warning: Could not save the run index entry.\n' >&2
	fi
	if [[ -n $index_script && -n ${FLEET_RUN_ID:-} ]]; then
		python3 -B "$index_script" finish --code "$result" --error "${run_error:-Setup stopped during $run_stage (exit $result).}" || log_warn 'Could not finish the run index entry.'
	fi
	if [[ $summary_expected == 1 && ! -e $DOTFLEET_SUMMARY_MARKER ]] && declare -F log_error >/dev/null; then
		# Early failures have an index entry, but no apply review.
		log_error "Setup stopped (exit $result) before the run renderer completed."
		if [[ -e $DOTFLEET_APPLY_MARKER ]]; then
			log_warn 'Inspect the latest ~/.fleet/backups/ folder for RUN.json before retrying.'
		else
			log_info 'No apply review was saved. Use fleet logs show to read the attempt.'
		fi
	fi
	rm -rf -- "$work_dir"
	exit "$result"
}
trap finish_run EXIT
trap 'run_error="Setup failed during $run_stage: ${BASH_COMMAND%% *} exited $?."' ERR
if [[ -n $script_dir && -r $script_dir/scripts/logger.sh ]]; then
	logger=$script_dir/scripts/logger.sh
else
	command -v curl >/dev/null || {
		run_error='Missing required command: curl.'
		printf '%s\n' "$run_error" >&2
		exit 1
	}
	mkdir "$work_dir/scripts"
	curl -fsSL "$raw/scripts/logger.sh" -o "$work_dir/scripts/logger.sh"
	curl -fsSL "$raw/VERSION" -o "$work_dir/VERSION"
	logger=$work_dir/scripts/logger.sh
fi
# shellcheck source=scripts/logger.sh
source "$logger"
usage() {
	log_section "Fleet help"
	log_heredoc '' <<EOF
Fleet $DOTFILES_VERSION manages home configuration with a saved machine profile.
Usage: bash install.sh [OPTIONS]
       curl -fsSL $raw/install.sh | bash
Options:
  --adopt          Create a local repository from existing home files. Exclude secret matches before committing.
  --agents NAMES   Configure detected coding assistants, separated by commas; none skips this step.
  --configure      Run the enrollment wizard again. Review every choice before applying.
  --defaults       Skip prompts only with a complete profile. New groups default to on.
                   This flag cannot bypass missing identity, domain confirmation, or floating tool versions.
  --domain NAME    Record a domain choice. The content declares each domain (described below).
  --format FORMAT  plain=real names; chezmoi=native sources. A manifest takes priority.
  --git-email EMAIL Record the Git author email.
  --git-name NAME  Record the Git author name.
  --group NAME=on|off Include or exclude a content group. Repeat for more groups.
  -h, --help       Print full help.
  --machine-name NAME Name this machine in the fleet at first enrollment. Fleet never renames it.
  --repo URL       Content Git repository; default: https://github.com/jeremyspofford/dotfiles.
                   Plain repositories use real names. Chezmoi repositories use native source names.
  --tool TOOL=VERSION Record a full tool version, such as node=24.14.1. Repeat for more tools.
  --update-bash    Accept installing bash with Homebrew when the found bash is older than 4.4.
A profile saves the repository, domain, Git author identity, tool versions, and content groups.
Domains choose optional additions from your content repository. Identity and tools are chosen separately.
The content declares its domains in fleet.toml. Core always applies.
Content without declarations offers core, personal, work, and client.
The bundled content declares these domains:
  core      Base configuration only.
  personal  Your own settings; bundled examples show your directory and list its entries.
  work      Employer settings; bundled examples show your directory and list its entries.
  client    Customer settings; bundled examples show your directory and list its entries.
Domain descriptions and command names come from your content. Empty overlays say they add nothing yet.
The domain prompt inspects the selected checkout and lists its domain command additions.
Custom repositories can define other behavior; check their documentation when additions are not described.
A new profile defaults to core. Later prompts show the domain saved in ~/.fleet/profile.toml.
Press Enter to confirm that domain, or type another domain name and press Enter.
The profile also saves Git identity, exact tool versions, and group choices. Mise tracks installed versions.
Groups select optional files or alias blocks. Each prompt defines on and off.
The wizard runs for new or incomplete profiles, unchosen domains, and --configure.
Steps: welcome, scan, source, domain, identity, tools, groups, agents, review, apply.
Source choices: adopt keeps existing files; defaults uses our examples; repo uses your Git URL.
Adoption scans root dotfiles and .config files. It excludes credential and shell-history names, secret patterns, binary files, and files above 2 MiB.
The scan cannot prove a file contains no secrets. Review the local repository before pushing it.
Review lists planned software, checkout, file, and profile changes, plus locations and next steps. Type confirm to apply, quit to cancel, or a step name to go back.
Old stores move to the Fleet paths before setup. Profiles and backup folders stay intact.
The review lists those moves. Cancelling apply keeps them. Other home changes wait for confirmation.
Temporary tool downloads stay outside your home. Each attempt keeps a small private entry, even before apply.
Use fleet logs list or fleet logs show to read attempts. The newest 100 entries stay; read-only commands add none.
Adopted file contents stay unchanged, including existing Mise files. Profile choices do not rewrite adopted files.
Tools use full versions. Enter accepts the prompt's stated action; skip leaves a choice incomplete.
Complete profiles need no profile prompts. Every summary prints all six categories, including scripted runs.
Empty categories say no changes. Paths start with ~ for your home. RUN.json keeps full paths and commit IDs.
Skipped gaps appear under Warnings. The last lines show status and the RUN.json location.
Without a terminal, missing identity, an unconfirmed domain, or floating tool versions stop setup.
Supply the listed flags to complete those values. New group defaults are printed before applying files.
Prompts read /dev/tty, the terminal device, never piped input.
Headings, input, and warnings use the shared logger's colors. Choices also have text labels.
Set NO_COLOR to disable colors, even with an empty value. Redirected output and TERM=dumb remain plain.
Requires bash 4.4+, git, curl, tar, and Python 3.11+. Supports Linux, macOS, and WSL (Windows Subsystem for Linux).
macOS ships bash 3.2. Fleet then asks to install bash with Homebrew before anything else changes.
Type yes to install it, or stop to quit. Without a terminal, only --update-bash gives that answer.
Without Homebrew, setup stops and names the Homebrew install command. Fleet never installs Homebrew.
Missing chezmoi and Mise go in ~/.local/bin. Setup never uses sudo or installs content-default tools.
Content lives in ~/.fleet/dotfiles/NAME. Backups live in ~/.fleet/backups/<timestamp>/.
The installer adds ~/.local/bin/dotfleet. Use its --help for updates, checks, rollback, and saved choices.
The manager checkout lives in ~/.fleet/manager. Software results include the installed command.
Setup and update maintain ~/.fleet/tui, a private Python environment for Textual's terminal interface.
Its packages are separate from system Python. Software results record environment changes; unchanged environments stay untouched.
The plain commands work without Textual. The tui subcommand shows plain alternatives when its environment is missing.
Review MANIFEST.json before restoring backup home/ files and recorded external targets.
Native chezmoi templates and hooks can execute code. Use only trusted content repositories.
Examples:
  bash install.sh --domain personal --git-name "Example Person" --git-email person@example.invalid --tool node=24.14.1
  bash install.sh --repo git@github.com:you/dotfiles.git --configure
Author: Jeremy Spofford / Aria Labs; Version: $DOTFILES_VERSION (VERSION).
EOF
}
arguments=("$@")
while [[ $# -gt 0 ]]; do
	case $1 in
	-h | --help)
		summary_expected=0
		usage
		exit 0
		;;
	--configure | --defaults | --adopt | --update-bash) shift ;;
	--repo | --format | --domain | --git-name | --git-email | --machine-name | --pin | --tool | --group | --agents)
		[[ $# -ge 2 && -n $2 && $2 != --* ]] || {
			usage >&2
			exit 2
		}
		shift 2
		;;
	*)
		usage >&2
		exit 2
		;;
	esac
done
# Bootstrap layer: this block and scripts/bash_floor.sh run on bash 3.2. See docs/STYLE.md.
# macOS ships bash 3.2. Fleet's scripts need 4.4, so find or install one, then run again with it.
if [ "${BASH_VERSINFO[0]}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
	run_stage="bash check"
	run_error="Fleet needs bash 4.4 or newer; found $BASH_VERSION at $BASH."
	if [ -n "${FLEET_BASH_BOOTSTRAPPED:-}" ]; then
		error_exit "Fleet started a resolved bash, but it reports $BASH_VERSION at $BASH. Fleet needs bash 4.4 or newer."
	fi
	update_bash=no
	# The parser above shifted every argument. An empty array needs this form on bash 3.2.
	for argument in ${arguments[@]+"${arguments[@]}"}; do
		if [ "$argument" = --update-bash ]; then update_bash=yes; fi
	done
	if [ -n "$script_dir" ]; then
		# shellcheck source=scripts/bash_floor.sh
		. "$script_dir/scripts/bash_floor.sh"
	else
		floor_text=$(curl -fsSL "$raw/scripts/bash_floor.sh") || error_exit "Could not download $raw/scripts/bash_floor.sh."
		eval "$floor_text"
	fi
	modern_bash=$(fleet_ensure_bash "$update_bash") || exit 1
	log_info "Running Fleet with bash at $modern_bash"
	export FLEET_BASH_BOOTSTRAPPED=1
	# The new process makes its own work folder and run entry.
	trap - EXIT
	rm -rf -- "$work_dir"
	if [ -n "$script_dir" ]; then exec "$modern_bash" "${BASH_SOURCE[0]}" ${arguments[@]+"${arguments[@]}"}; fi
	installer=$(curl -fsSL "$raw/install.sh") || error_exit "Could not download $raw/install.sh."
	exec "$modern_bash" -c "$installer" install.sh ${arguments[@]+"${arguments[@]}"}
fi
# Every Fleet script in this run uses this bash: Python reads FLEET_BASH, and env finds the link.
export FLEET_BASH=$BASH
kept_arguments=()
for argument in "${arguments[@]}"; do
	if [[ $argument != --update-bash ]]; then kept_arguments+=("$argument"); fi
done
arguments=("${kept_arguments[@]}")
# Start before preflight. Reuse the installed writer if a bootstrap download fails.
if [[ -n $script_dir && -r $script_dir/scripts/run_index.py ]]; then
	index_script="$script_dir/scripts/run_index.py"
elif [[ -r $HOME/.fleet/manager/scripts/run_index.py ]]; then
	index_script="$HOME/.fleet/manager/scripts/run_index.py"
elif [[ -r $HOME/.local/share/fleet/manager/scripts/run_index.py ]]; then
	index_script="$HOME/.local/share/fleet/manager/scripts/run_index.py"
else
	index_script="$work_dir/run_index.py"
	curl -fsSL "$raw/scripts/run_index.py" -o "$index_script"
fi
if command_exists python3; then
	FLEET_RUN_ID=$(python3 -B "$index_script" begin --command "$run_command")
	export FLEET_RUN_ID
fi
missing_commands=()
for prerequisite in git curl tar python3; do
	if ! command_exists "$prerequisite"; then missing_commands+=("$prerequisite"); fi
done
if [[ ${#missing_commands[@]} != 0 ]]; then
	run_error="Missing required commands: ${missing_commands[*]}."
	error_exit "$run_error"
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
	run_error='Python 3.11 or newer is required.'
	error_exit "$run_error"
}
run_stage="manager checkout"
python3 -B "$index_script" stage --name "$run_stage"
if [[ -z $script_dir ]]; then
	script_dir="$work_dir/manager"
	git clone "$manager_repo" "$script_dir" </dev/null
fi
run_stage="store migration"
python3 -B "$index_script" stage --name "$run_stage"
python3 -B "$script_dir/scripts/store_migration.py" --record "$work_dir/store-moves.json"
export DOTFLEET_STORE_MOVES_FILE="$work_dir/store-moves.json"
# An installed manager in an older store now runs from ~/.fleet.
for old in .local/share/dotfleet .local/share/fleet; do
	if [[ $script_dir == "$HOME/$old/"* ]]; then
		script_dir="$HOME/.fleet/${script_dir#"$HOME/$old/"}"
		index_script="$script_dir/scripts/run_index.py"
	fi
done
if [[ $index_script == "$HOME/.local/share/"*"/manager/scripts/run_index.py" ]]; then
	index_script="$HOME/.fleet/manager/scripts/run_index.py"
fi
case $(uname -s) in
Linux)
	host=Linux
	if [[ -n ${WSL_DISTRO_NAME:-} ]] || { [[ -r /proc/version ]] && grep -qi microsoft /proc/version; }; then host=WSL; fi
	;;
Darwin) host=macOS ;;
*) error_exit 'Supported systems: Linux, macOS, or WSL.' ;;
esac
log_section "Fleet setup"
log_info "Host: $host / $(uname -m)"
log_info "Bash: $BASH_VERSION at $BASH"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$work_dir/bash"
ln -s "$BASH" "$work_dir/bash/bash"
export PATH="$work_dir/bash:$PATH"
umask 077
run_stage="software preparation"
python3 -B "$index_script" stage --name "$run_stage"
mkdir -p "$work_dir/bin"
if ! command -v chezmoi >/dev/null; then
	log_info 'Preparing chezmoi; it moves to ~/.local/bin only after confirmation'
	curl -fsSL https://get.chezmoi.io -o "$work_dir/chezmoi-install"
	sh "$work_dir/chezmoi-install" -b "$work_dir/bin" </dev/null
	export DOTFLEET_STAGED_CHEZMOI="$work_dir/bin/chezmoi"
fi
if ! command -v mise >/dev/null; then
	log_info 'Preparing Mise; it moves to ~/.local/bin only after confirmation'
	curl -fsSL https://mise.run -o "$work_dir/mise-install"
	HOME="$work_dir" MISE_INSTALL_PATH="$work_dir/bin/mise" sh "$work_dir/mise-install" </dev/null
	export DOTFLEET_STAGED_MISE="$work_dir/bin/mise"
fi
export PATH="$work_dir/bin:$PATH"
run_stage="enrollment"
# Python uses the same logger style for output and terminal prompts.
python3 -u "$script_dir/scripts/manager.py" "${arguments[@]}" </dev/null
