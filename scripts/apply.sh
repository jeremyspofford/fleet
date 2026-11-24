#!/usr/bin/env bash
# Purpose: Apply files with explained terminal conflict choices.
# Usage: bash scripts/apply.sh [-h|--help]
# Parameters: -h/--help prints help; other arguments are invalid.
# Example: bash scripts/apply.sh
# Exit codes: 0 success/help; 1 apply failure; 2 invalid arguments.
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
# Change only the displayed menu. chezmoi still detects conflicts and handles choices.
# Its --no-tty mode reads full lines from our terminal descriptor, never piped stdin.
# Read one answer from the terminal and pass it to chezmoi.
# Record the prompt and the key, so Fleet can tell kept files from files never asked about.
answer() {
	local reply
	IFS= read -r reply <&3 || reply=''
	printf '%s\n' "$reply" >&4
	if [[ -n ${FLEET_APPLY_CHOICES:-} ]]; then
		printf '%s\t%s\n' "${reply:0:1}" "$1" >>"$FLEET_APPLY_CHOICES"
	fi
}

show_apply_output() {
	local char line='' choices suffix message
	# chezmoi reads its answers from this pipe; answer() writes them.
	exec 4>"$ANSWERS"
	while IFS= read -r -n 1 char; do
		if [[ -z $char ]]; then
			log_info "$line"
			line=''
			continue
		fi
		line+=$char
		# Current and older chezmoi versions differ in their skip-all option.
		# Directory conflicts have no diff choice.
		for choices in \
			'diff/overwrite/all-overwrite/skip/Skip-conflicts/quit' \
			'overwrite/all-overwrite/skip/Skip-conflicts/quit' \
			'diff/overwrite/all-overwrite/skip/quit' \
			'overwrite/all-overwrite/skip/quit' \
			'diff/yes/no/all/quit' \
			'yes/no/all/quit'; do
			suffix=" ($choices)? "
			if [[ $line != *"$suffix" ]]; then continue; fi
			message=${line%"$suffix"}
			log_info ''
			log_info 'File conflict'
			log_info '-------------'
			log_info "$message."
			log_info 'Type one letter, then press Enter. Enter alone asks again.'
			if [[ $choices == */Skip-conflicts/* ]]; then log_info 'Uppercase S differs from lowercase s.'; fi
			log_info ''
			if [[ $choices == diff/* ]]; then log_info '  d  Show changes for this file, then ask again.'; fi
			if [[ $choices == *yes/no/all/quit ]]; then
				log_info '  y  Apply the proposed change to this file.'
				log_info '  n  Keep this file unchanged; continue with other files. Recommended if unsure.'
				log_info '  a  Apply remaining changes, but still ask about conflicts.'
			else
				log_info '  o  Overwrite this file with the generated version.'
				log_info '  a  Overwrite this file and all remaining conflicts without asking again.'
				log_info '  s  Keep this file unchanged; continue with other files. Recommended if unsure.'
				if [[ $choices == */Skip-conflicts/* ]]; then
					log_info '  S  Keep all conflicting files unchanged; apply other changes.'
				fi
			fi
			log_info '  q  Stop applying. Changes already made remain in place.'
			choices=${choices//diff/d}
			choices=${choices//all-overwrite/a}
			choices=${choices//overwrite/o}
			choices=${choices//Skip-conflicts/S}
			choices=${choices//skip/s}
			choices=${choices//quit/q}
			choices=${choices//yes/y}
			choices=${choices//no/n}
			choices=${choices//all/a}
			log_info ''
			log_info "Choose [$choices]:"
			answer "$message"
			line=''
			break
		done
		# Do not hide an unfamiliar prompt from a newer chezmoi or custom template.
		if [[ $line == *'? ' ]]; then
			log_info "$line"
			log_info 'Type your answer, then press Enter.'
			answer "$line"
			line=''
		fi
	done
	if [[ -n $line ]]; then log_info "$line"; fi
}

log_info "Applying managed files"
if [[ ${FLEET_REMOTE_RUN:-0} != 1 ]] && { exec 3<>/dev/tty; } 2>/dev/null; then
	# A plain line prompt also lets users correct input before pressing Enter.
	# Answers pass through a pipe, so each choice is recorded before chezmoi reads it.
	ANSWERS_DIR=$(mktemp -d)
	ANSWERS=$ANSWERS_DIR/answers
	mkfifo -m 600 "$ANSWERS"
	chezmoi --no-tty --no-pager apply <"$ANSWERS" | show_apply_output
	rm -rf "$ANSWERS_DIR"
	exec 3>&-
else
	# Unattended runs must never guess how to resolve a local edit.
	if ! chezmoi --no-tty --error-on-conflict apply </dev/null; then
		log_error 'Apply stopped. Review changes in a terminal, then re-run setup.'
		exit 1
	fi
fi
