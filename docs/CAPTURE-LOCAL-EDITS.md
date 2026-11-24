# Capture local edits

Status: proposal. Section 8 lists the questions for the owner, each with a recommendation. Nothing here is built.

The owner asked how a person edits a dotfile on one machine, spreads the change to every machine, and keeps it managed.
An update must then neither raise a conflict nor overwrite the edit.
This document proposes a capture command, and an update that merges instead of asking.

## Terms

- Content is the dotfiles Git repository. The content checkout is its local copy under `~/.fleet/dotfiles`.
- A managed file is a home file that Fleet installs from content, such as `~/.zshrc`.
- The rendered file is what Fleet installs on this machine. It is the content file after group blocks and domain choices.
- Drift means a managed home file differs from its rendered file.
- To capture means to copy a drifted edit back into content, commit it, and push it.
- A hunk is one contiguous group of changed lines in a diff.
- A local companion is an unmanaged file that a managed file reads, such as `~/.zshrc.local`. Content already sources two of them.
- A three-way merge combines two edited versions of a file with their common ancestor, the base.

## 1. Today

1. A person edits content in the checkout, commits, pushes, and runs `fleet update`. The README states this flow.
2. An edit to a live home file is drift. `fleet status` and `fleet doctor` report it, and `fleet diff` shows it.
3. `fleet update` treats drift as a repair trigger. In a terminal, chezmoi asks per file whether to overwrite or skip.
4. Without a terminal, a conflict stops the run before any write. Fleet never chooses overwrite unattended.
5. Uncommitted edits to managed files in the checkout block every update.
6. Content sources `~/.zshrc.local` and `~/.bashrc.local` when they exist. Fleet neither installs nor records them.

So a home edit has no path back into content, and an upstream change to the same file always meets a prompt or a stop.

## 2. The capture command

This design proposes `fleet capture [FILE ...]`. Without FILE, it offers every drifted managed file.

1. Fleet fetches content. When the checkout is behind, it fast-forwards the checkout only. It writes no home file yet.
2. For each file, Fleet builds the edit as a patch: the home file against the rendered file that this machine has installed.
3. Fleet applies that patch to the content file with a three-way merge.
   The base is the content file at the installed commit. The other side is the content file at the fetched commit.
4. Fleet shows the resulting change to the content file, hunk by hunk. Section 3 lists the choices per hunk.
5. Fleet runs the secret check on every line that the capture adds (section 6).
6. Fleet shows a review of all files and asks one question: type `capture` and press Enter, or press Enter alone to cancel.
7. Fleet commits only the captured files in the checkout. The message is `capture: FILE from MACHINE`.
8. Fleet asks whether to push. Section 8, question 2, covers this choice.
9. Fleet then runs the normal update for this machine. The home file now equals its rendered file, so no drift remains.

Rules:

- Capture needs a terminal. Every prompt shows its keys and states that Enter is required.
- A hunk that changes a group marker line, or that falls inside a block that this machine omits, is refused. Fleet names the group.
- The patch keeps the omitted blocks of disabled groups. Other machines still receive them.
- A three-way conflict in step 3 stops that file. Fleet names it and changes nothing for it.
- Native chezmoi content and templates are out of the first build. Fleet points to `chezmoi re-add` for them.

## 3. Machine-only lines and fleet-wide lines

Each hunk gets one of three choices:

- `f`, fleet-wide: the hunk goes into content and reaches every machine.
- `m`, machine-only: the hunk moves to the file's local companion on this machine. The managed file returns to its rendered form.
- `s`, skip: the hunk stays as drift. A later capture or update asks again.

A file without a local companion offers only `f` and `s`.
Content declares companions in `fleet.toml`, because only content knows which files source which (section 8, question 3):

```toml
[local]
".zshrc" = ".zshrc.local"
".bashrc" = ".bashrc.local"
```

Fleet never captures a local companion and never writes one, except by the `m` choice. A companion holds machine-only lines by design.
Groups stay the tool for optional fleet-wide blocks. Domains stay the tool for role-specific files.

## 4. Update with uncaptured edits

Today an update asks or stops for every drifted file. This design proposes:

1. Content did not change the file in this update: Fleet leaves the home file as it is and prints one line.
   "~/.zshrc has uncaptured edits. Run fleet capture to keep them, or fleet diff to see them." There is no prompt and no stop.
2. Content did change the file: Fleet runs a three-way merge. The base is the previous rendered file, which is the installed source.
   One side is the home file, and the other side is the new rendered file.
   - A clean merge: in a terminal, Fleet shows it and offers `m` (write the merge), `c` (capture my edits first), `d` (diff), or `s` (skip).
     Unattended, Fleet writes the clean merge and reports it. The local edits survive as drift.
   - A conflicting merge: in a terminal, the same choices without `m`. Unattended, Fleet skips the file and reports it. The rest of the update applies.
3. Fleet never overwrites an uncaptured edit without a typed choice. Skip replaces today's stop for unattended runs (section 8, question 4).
4. The backup copies each changed home file before any write, as today.

## 5. Reaching the other machines

1. After a capture and a push, each machine receives the change at its next `fleet update`. `fleet update --all` reaches every connected member at once.
2. A machine without its own edits to that file takes the new rendered file. No prompt appears.
3. A machine with its own uncaptured edits to that file follows section 4, step 2. A clean merge keeps both; a conflict asks or skips.
4. A machine that has turned a group off still omits that group's block, as today.

## 6. Secrets

1. Capture runs the adoption scan's secret check (`SECRET` in `scripts/enrollment.py`) on every added line, before the review.
2. A match refuses that hunk. Fleet names the file and line and shows no value.
   It suggests a `[secrets]` reference in `fleet.toml` and a `~/.secrets` variable instead.
3. Capture refuses whole files that adoption excludes: credential file names, binary files, and files above 2 MiB.
4. The check cannot prove that no secret remains. The review says so before the push, as adoption does today.

## 7. Build order

The smallest first PR is `fleet capture FILE` for plain and Stow content, whole-file, fleet-wide only:

1. Steps 1-9 of section 2, with one choice per file instead of per hunk: `capture` or cancel.
2. The secret check of section 6, and the refusal of group-marker changes.
3. A three-way conflict in step 3 stops that file.
4. Tests:
   - A capture reaches a second test home through `fleet update`.
   - A disabled group's block survives a capture, and a secret is refused.
   - A checkout that is behind merges cleanly.

Later PRs add the per-hunk choices and local companions (section 3), then the update merge (section 4).

## 8. Questions for the owner

1. What is the command called? Options: `fleet capture`, `fleet save`, or `fleet keep`. Recommendation: `fleet capture`. It says that a home edit moves into content.
2. May Fleet push content? Today Fleet never pushes. Options: (a) ask at the end, with no as the default; (b) push after the capture confirmation; (c) never push, and print the `git push` command.
   Recommendation: (a). The change reaches other machines only after a push, and content may be public.
3. How does content mark machine-only lines? Options: (a) a `[local]` table in `fleet.toml` names each companion; (b) a fixed rule, FILE plus `.local`.
   Recommendation: (a). Content already chooses its companion names, and not every file can source one.
4. Update with uncaptured edits: today it asks in a terminal and stops when unattended. Options:
   (a) Section 4: leave an unchanged file, merge a changed one, and skip a conflict unattended.
   (b) Keep today's prompt and stop.
   Recommendation: (a). The rest of the fleet keeps updating, and no edit is lost or overwritten.
