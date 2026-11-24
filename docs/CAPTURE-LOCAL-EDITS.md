# Capture local edits

Status: proposal, fully ruled. The owner ruled on all eight questions on 2026-11-24. Section 9 records each ruling. Nothing here is built.

The owner asked how a person edits a dotfile on one machine, spreads the change to every machine, and keeps it managed.
An update must then neither raise a conflict nor overwrite the edit.
This document proposes a capture command, a way to take over content as one's own repository, and an update that merges instead of asking.

## Terms

- Content is the dotfiles Git repository. The content checkout is its local copy under `~/.fleet/dotfiles`.
- A managed file is a home file that Fleet installs from content, such as `~/.zshrc`.
- The rendered file is what Fleet installs on this machine. It is the content file after group blocks and domain choices.
- Drift means a managed home file differs from its rendered file.
- To capture means to copy a drifted edit back into content, commit it, and push it.
- A hunk is one contiguous group of changed lines in a diff.
- A local partner is the unmanaged file named after a managed file plus `.local`, such as `~/.zshrc.local`. Content already sources two of them.
- A three-way merge combines two edited versions of a file with their common ancestor, the base.
- `origin` is the Git remote that the checkout pulls from and pushes to. `upstream` is a second remote for the base repository.
- An own repository is one that the user can push to. A writable `origin` counts as own.

## 1. Today

1. A person edits content in the checkout, commits, pushes, and runs `fleet update`. The README states this flow.
2. An edit to a live home file is drift. `fleet status` and `fleet doctor` report it, and `fleet diff` shows it.
3. `fleet update` treats drift as a repair trigger. In a terminal, chezmoi asks per file whether to overwrite or skip.
4. Without a terminal, a conflict stops the run before any write. Fleet never chooses overwrite unattended.
5. Uncommitted edits to managed files in the checkout block every update.
6. Content sources `~/.zshrc.local` and `~/.bashrc.local` when they exist. Fleet neither installs nor records them.
7. Many users start from another person's repository or from the example. They cannot push there.
   Adopted content starts with no remote at all.
8. Fleet never pushes today.

## 2. The capture command

`fleet capture [FILE ...]` (ruling 1). Without FILE, it offers every drifted managed file.

1. Fleet fetches content. When the checkout is behind, it fast-forwards the checkout only. It writes no home file yet.
2. For each file, Fleet builds the edit as a patch: the home file against the rendered file that this machine has installed.
3. Fleet applies that patch to the content file with a three-way merge.
   The base is the content file at the installed commit. The other side is the content file at the fetched commit.
4. Fleet shows the resulting change to the content file, hunk by hunk. Section 4 lists the choices per hunk.
5. Fleet runs the secret check on every line that the capture adds (section 7).
6. Fleet shows a review of all files and asks one question: type `capture` and press Enter, or press Enter alone to cancel.
7. Fleet commits only the captured files in the checkout. The message is `capture: FILE from MACHINE`.
8. Fleet checks whether `origin` is the user's own repository, with `git push --dry-run origin HEAD`.
   It sets `GIT_TERMINAL_PROMPT=0`, so the check never waits for a password.
   - Own: Fleet pushes to `origin` without a question (rulings 2 and 8).
   - Not own, or no remote: Fleet keeps the commit local and offers section 3's flow. It never pushes to another person's repository.
9. Fleet then runs the normal update for this machine. The home file now equals its rendered file, so no drift remains.

Rules:

- Capture needs a terminal. Every prompt shows its keys and states that Enter is required.
- A hunk that changes a group marker line, or that falls inside a block that this machine omits, is refused. Fleet names the group.
- The patch keeps the omitted blocks of disabled groups. Other machines still receive them.
- A three-way conflict in step 3 stops that file. Fleet names it and changes nothing for it.
- Fleet never pushes to `upstream`.
- Native chezmoi content and templates are out of the first build. Fleet points to `chezmoi re-add` for them.

## 3. Your own content repository

This design proposes `fleet dotfiles own`. Capture also offers it when `origin` is not the user's own.
It turns the active checkout into the user's own repository and keeps its history.

1. Fleet checks `origin` as in section 2, step 8. When it is already own, Fleet says so and stops.
2. Fleet asks where the own repository lives. The keys are:
   - `g`: create it with `gh repo create NAME`, when `gh` is installed and signed in. Fleet asks for its visibility each time, with private preselected (ruling 5).
   - `u`: type the address of an empty repository that the user created.
   - `c`: cancel. Nothing changes.
3. Fleet renames the old `origin` to `upstream`. Adopted content has no old remote, so it gets no `upstream`.
4. Fleet adds the own repository as `origin` and pushes every branch and tag. The history stays whole.
5. Fleet records the new address as this machine's content source in its profile, and in the registry record.
6. Fleet offers to switch the other members now (section 6).
7. `fleet dotfiles merge-base` later fetches `upstream` and merges its new commits into the checkout. Updates never merge them (ruling 7).
   It shows the review and asks for `merge` and Enter. Then it commits and pushes to `origin`.
   A merge conflict stops it, names each file, and changes nothing.

Fleet needs Git credentials for the push. It uses the user's existing ones, such as a `gh` sign-in or an SSH key.
The trust-bootstrap step of item D can provide them later. Fleet asks for no password.

## 4. Machine-only lines and fleet-wide lines

Each hunk gets one of three choices:

- `f`, fleet-wide: the hunk goes into content and reaches every machine.
- `m`, machine-only: the hunk moves to the file's local partner on this machine. The managed file returns to its rendered form.
- `s`, skip: the hunk stays as drift. A later capture or update asks again.

The local partner is the file name plus `.local`, such as `~/.zshrc.local` (ruling 3). No table in `fleet.toml` declares it.
Fleet offers `m` only when the content file names its partner, such as `.zshrc.local` inside `.zshrc`. Otherwise the hunk could not take effect.
Fleet never captures a local partner and never writes one, except by the `m` choice.
Groups stay the tool for optional fleet-wide blocks. Domains stay the tool for role-specific files.

## 5. Update with uncaptured edits

This is ruling 4:

1. Content did not change the file in this update: Fleet leaves the home file as it is and prints one line.
   "~/.zshrc has uncaptured edits. Run fleet capture to keep them, or fleet diff to see them." There is no prompt and no stop.
2. Content did change the file: Fleet runs a three-way merge. The base is the previous rendered file, which is the installed source.
   One side is the home file, and the other side is the new rendered file.
   - A clean merge: in a terminal, Fleet shows it and offers `m` (write the merge), `c` (capture my edits first), `d` (diff), or `s` (skip).
     Unattended, Fleet writes the clean merge and reports it. The local edits survive as drift.
   - A conflicting merge: in a terminal, the same choices without `m`. Unattended, Fleet skips the file and reports it. The rest of the update applies.
3. Fleet never overwrites an uncaptured edit without a typed choice. Skip replaces today's stop for unattended runs.
4. The backup copies each changed home file before any write, as today.

## 6. Reaching the other machines

1. After a capture and a push, each machine receives the change at its next `fleet update`. `fleet update --all` reaches every connected member at once.
2. A machine without its own edits to that file takes the new rendered file. No prompt appears.
3. A machine with its own uncaptured edits to that file follows section 5, step 2. A clean merge keeps both; a conflict asks or skips.
4. A machine that has turned a group off still omits that group's block, as today.
5. After `fleet dotfiles own`, the other members still pull from the old address. Fleet switches every reachable member (ruling 6).
   Fleet sends a new remote profile action, `dotfiles source --repo URL`, to each connected member.
   Each target saves it, and its next update re-points its checkout's `origin` and keeps `upstream`. The history matches, so the move is a fast-forward.
   Fleet prints the same command for each member that it cannot reach.
6. A target with unpushed local commits, or with uncommitted edits to managed files, keeps its old source. It reports why, as today's update gate does.

## 7. Secrets

1. Capture runs the adoption scan's secret check (`SECRET` in `scripts/enrollment.py`) on every added line, before the review.
2. A match refuses that hunk. Fleet names the file and line and shows no value.
   It suggests a `[secrets]` reference in `fleet.toml` and a `~/.secrets` variable instead.
3. Capture refuses whole files that adoption excludes: credential file names, binary files, and files above 2 MiB.
4. The check cannot prove that no secret remains. The review says so before the push, as adoption does today.
5. `fleet dotfiles own` pushes the existing history, which earlier secret checks did not see. Its review says so, and private is preselected (ruling 5).

## 8. Build order

The smallest first PR is `fleet capture FILE` for plain and Stow content, whole-file, fleet-wide only:

1. Steps 1-9 of section 2, with one choice per file instead of per hunk: `capture` or cancel.
2. The push only to an own `origin`. Otherwise the commit stays local, and Fleet prints the `fleet dotfiles own` step.
3. The secret check of section 7, and the refusal of group-marker changes.
4. A three-way conflict in step 3 stops that file.
5. Tests:
   - A capture pushes to a writable test remote and reaches a second test home through `fleet update`.
   - A capture against a read-only remote commits locally and pushes nothing.
   - A disabled group's block survives a capture, and a secret is refused.

Later PRs, in order:

1. `fleet dotfiles own` and `merge-base` (section 3), with the remote source action (section 6, point 5).
2. Per-hunk choices and local partners (section 4).
3. The update merge (section 5).

## 9. Questions for the owner, with rulings

The owner ruled on all eight questions on 2026-11-24. Each entry keeps its recommendation as the record of the advice given.

1. What is the command called? Options: `fleet capture`, `fleet save`, or `fleet keep`. Recommendation: `fleet capture`.
   RULED: `fleet capture`.
2. May Fleet push content? Recommendation: ask at the end, with no as the default.
   RULED: yes, when the content repository is the user's own. Fleet must let a user who started from another person's repository make their own one (section 3).
3. How does content mark machine-only lines? Recommendation: a `[local]` table in `fleet.toml`.
   RULED, against the recommendation: the fixed rule, the file name plus `.local`.
4. Update with uncaptured edits. Recommendation: leave an unchanged file, merge a changed one, and skip a conflict unattended.
   RULED: yes, as recommended (section 5).
5. What visibility does `fleet dotfiles own` give a repository that it creates with `gh`?
   Options: (a) private, (b) public, (c) ask each time with private preselected.
   Recommendation: (c). Old history may hold values that no secret check saw, and a person can still choose public.
   RULED: (c). Fleet asks each time, with private preselected.
6. How do the other members switch to the new repository? Options:
   (a) Fleet sends `dotfiles source` to each connected member at once, and prints the command for the rest.
   (b) The user runs `fleet dotfiles use` on each machine.
   (c) Members learn it from the registry exchange and switch at their next update.
   Recommendation: (a). It reaches every member that Fleet can reach, and nothing switches without a person's command.
   RULED: (a). Fleet switches every reachable member.
7. How do base changes from `upstream` arrive? Options: (a) only through `fleet dotfiles merge-base`, with a review; (b) every update merges them.
   Recommendation: (a). A base change can conflict with the user's own edits, and that needs a person.
   RULED: (a). Base changes arrive only through `fleet dotfiles merge-base`.
8. Does capture push without a question once `origin` is own? Options: (a) yes; (b) ask each time, with yes preselected.
   Recommendation: (a). Ruling 2 says yes, and the review before the commit already asks for `capture`.
   RULED: (a). Capture pushes without asking once `origin` is own.
