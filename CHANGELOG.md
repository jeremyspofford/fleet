# Changelog

This file follows Keep a Changelog. Versions use SemVer: major.minor.patch.

## [0.11.0] - 2026-11-25

### Added

- fleet dotfiles own and fleet dotfiles source (#98)
- capture a whole edited file into plain or Stow content (#96)
- ask every first enrollment for a name, record install stages, and end with a check (#85)
- apply the owner's agent rulings on skills, local extensions, uninstall, and secrets (#82)
- apply the owner's agent rulings on user values, core, rules, and plugins (#81)
- support sibling WSL distros on one Windows host (#77)
- add fleet tools adopt to record installed tool versions (#75)

### Changed

- Claude Opus 5.5 <noreply@anthropic.com>
- propose capturing local dotfile edits into content (#89)
- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo
- set the update-check state the display tests expect (#95)
- https://claude.ai/code/session_01VHXAyaCJMYbiMbEbM2uXiY
- pin the first draft's conflict rule to Fleet 0.5.0 (#91)
- prove an unknown member field is dropped, not fatal (#83)
- record the owner's rulings on seven more agent module points (#79)
- record the owner's rulings on five agent module points (#78)
- mark the agent module's open points against main (#74)

### Fixed

- report a file kept at the conflict prompt as kept by you, not a failure (#90)
- small follow-ups from reviews F-070 and F-082 (#97)
- read the saved machine name from the home being worked on (#93)
- write Fleet's Mise fragment before the handover retires the old file (#92)
- one version form for the proposal and the rule, and firmer name tests (#94)
- close the version-name edges that review F-044 found (#88)
- remove the causes of two CI flakes (#87)
- preview the Mise handover in fleet diff, and name drifted files in doctor (#86)
- scan TOML strings fully, keep CRLF, and release every entry of a held plugin (#84)
- keep mise use -g out of Fleet's files and explain kept conflicts (#80)
- show agents, groups, domain, secrets, and addons lists as rows (#76)
- show lists as a heading and one aligned row per item (#73)
- identify machines by a stable ID and name them by their hostname (#72)
- write the Mise config with [tools] and [settings] tables (#71)
- say what the Agents choice does and what the content declares (#69)

## [0.10.0] - 2026-11-19

### Added

- update an old bash with Homebrew before Fleet's scripts run (#68)

### Changed

- Claude Opus 5.5 <noreply@anthropic.com>
- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo

### Fixed

- rename the marketplace package folder to ~/.fleet/marketplace-packages (#66)

## [0.9.0] - 2026-11-19

### Added

- keep Fleet's state and dotfiles library under ~/.fleet (#63)
- resolve the newest tool version with tools update (#64)
- update every fleet machine with update --all (#61)
- accept both singular and plural command group names (#60)

### Changed

- Claude Opus 5.5 <noreply@anthropic.com>
- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo

### Fixed

- remove exit codes from help at every level (#65)
- keep each machine's recorded manager version current (#62)
- name every prompt choice plainly and state what Enter does (#59)
- make Enter confirm at review and undo store moves on cancel (#57)

## [0.8.0] - 2026-11-19

### Added

- render Antigravity's MCP entry, the surface a tested install confirmed (#56)

### Changed

- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo
- Claude Opus 5.5 <noreply@anthropic.com>
- name the marketplace feature Fleet Marketplace (#53)

### Fixed

- keep the downloaded run index writer free of manager imports (#55)

## [0.7.0] - 2026-11-18

### Added

- exchange registry digests on contact and uninstall members remotely (#52)
- deploy Fleet to another machine and register both ways (#51)
- write plugin listings where agents install plugins themselves (#50)

### Changed

- Claude Opus 5.5 <noreply@anthropic.com>
- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo
- Claude Fable 5.2 <noreply@anthropic.com>
- design the Layer 2 agent-configuration module (#35)

## [0.6.0] - 2026-11-17

### Added

- print agents-table entries from an agent's existing settings (#47)
- serve every MCP server through one Fleet gateway entry (#46)
- update and pin marketplaces and report their results (#45)
- install agent packages from marketplaces (#44)
- manage a connected machine's profile choices over SSH (#43)
- add per-machine agent exceptions and report agent drift (#42)
- install and remove declared plugins through each agent (#41)
- deploy agent configuration through one Fleet-owned tree (#40)
- let unmanaged content files stay invisible to updates (#39)
- validate the agents table and add detect-only agents (#37)
- let content declare its domains (#36)

### Changed

- https://claude.ai/code/session_01VHXAyZNQYaE3KaWYVP4sXo
- Claude Opus 5.5 <noreply@anthropic.com>
- propose bridge plugins for Pi and OpenCode hooks (#48)

## [0.5.0] - 2026-11-17

### Added

- keep bounded run attempts and expose logs
- uninstall extra tool versions with clear argument errors
- consolidate backup actions and tool commands
- check updates before backups and add confirmed cleanup
- unify command actions and terminal access
- simplify connections and show fleet topology
- run Fleet commands on enrolled machines over SSH

### Fixed

- scope help to each command and group backup creation

## [0.4.0] - 2026-11-17

### Added

- add Fleet terminal interface

## [0.3.0] - 2026-11-17

### Added

- rename Dotfleet to Fleet and migrate manager stores (#22)
- check manager and content updates against their upstreams (#21)
- share logger styling across command output
- add human commands for owned stores
- add recorded fleet registry and finalize add-on contracts (#14)
- add recorded fleet registry and finalize add-on contracts
- record machines in the manager fleet registry
- define the manager add-on contract and decisions
- add dotfleet commands for enrolled machines (#11)
- add dotfleet commands for enrolled machines
- render readable run results from one record (#10)
- render readable run results from one record
- report planned and completed run changes (#8)
- report planned and completed run changes

### Changed

- apply code standards without changing command behavior
- route profile changes through commands
- integrate shared output styling
- integrate owned-store commands
- integrate read-only diff fix
- define registry and add-on terms in command help
- integrate fleet command provenance
- integrate readable run output
- preserve fleet records across rollback and uninstall
- record machine home paths in the registry schema

### Fixed

- show Fleet and available command names
- use logger section headers in help and prompt checks
- use per-agent toggles and detect installed assistants
- summarize wizard scans and keep source defaults consistent
- keep scan and agent imports together
- reuse scanned tools for agent detection
- show read-only file patches in diff
- wrap command help and keep one usage prefix
- keep registry and profile ownership in the manager
- make legacy domain provenance explicit (#13)
- make legacy domain provenance explicit
- compact unchanged runs and bound review output (#12)
- compact unchanged runs and bound review output
- explain unchosen domains in fleet commands
- preserve changed external targets before rollback
- require a terminal to reopen the wizard

## [0.2.0] - 2026-11-16

### Added

- add reviewed enrollment wizard (#7)
- add reviewed enrollment wizard
- add Dotfleet manager and plain content conversion

### Changed

- correct manager inventory and release review guidance
- verify interactive login shells during enrollment

### Fixed

- clarify domain additions and format terminal prompts (#6)
- clarify domain additions and format terminal prompts
- explain domain choices and saved defaults
- validate enrollment profiles and reconcile chezmoi config state
- preserve migrated Mise settings and test merged content

## [0.1.0]

### Added

- Manage plain dotfiles, machine profiles, backups, and generated settings.
