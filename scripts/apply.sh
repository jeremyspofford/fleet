#!/usr/bin/env bash
# Purpose: Apply files with explained terminal conflict choices.
# Usage: bash scripts/apply.sh [-h|--help]
# Parameters: -h/--help prints help; other arguments are invalid.
# Example: bash scripts/apply.sh
# Exit codes: 0 success or help; 2 invalid arguments.
#   Without a terminal: 1 when the apply stops at a conflict or fails.
#   In a terminal: apply_terminal.py's code: chezmoi's own code, 128 plus a signal number,
#   or 1 when the helper itself fails.
# Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/logger.sh
source "$SCRIPT_DIR/logger.sh"
usage() {
	log_section "Apply help"
	log_info "Usage: bash scripts/apply.sh [-h|--help] ($DOTFILES_VERSION)"
	log_info 'Apply files through chezmoi. Conflict prompts explain keys and require Enter.'
	log_info 'Parameters: -h, --help prints help. Example: bash scripts/apply.sh'
}
if [[ $# -gt 0 ]]; then
	case $1 in -h | --help)
		usage
		exit 0
		;;
	*)
		usage >&2
		exit 2
		;;
	esac
fi
log_info "Applying managed files"
if [[ ${FLEET_REMOTE_RUN:-0} != 1 ]] && { : </dev/tty; } 2>/dev/null; then
	# chezmoi runs on its own pseudo-terminal, so content scripts read input as without Fleet.
	# The helper explains each conflict menu and records only the answers to those menus.
	exec python3 "$SCRIPT_DIR/apply_terminal.py"
else
	# Unattended runs must never guess how to resolve a local edit.
	if ! chezmoi --no-tty --error-on-conflict apply </dev/null; then
		log_error 'Apply stopped. Review changes in a terminal, then re-run setup.'
		exit 1
	fi
fi
