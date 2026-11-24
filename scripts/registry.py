"""Keep recorded machine facts in the manager store. Never query other machines.

Internal module. Use dotfleet machines --help for the command interface.

The installer refreshes this machine after a backed-up run. Read-only commands
read the file without refreshing timestamps. Profile identity and secrets stay out.

Usage: import this module from Fleet.
Help: python3 scripts/registry.py --help.
Direct execution without help keeps its existing behavior.
"""

import datetime
import fcntl
import os
import socket
from pathlib import Path
import platform
import re
import uuid

from common import atomic, read_toml, run, value
import run_index
from store_migration import relocated
import dotfiles_library

REGISTRY = ".fleet/registry.toml"
FIELDS = {
    "hostname": str,
    "home": str,
    "os": str,
    "domain": str,
    "domain_chosen": bool,
    "content_repo": str,
    "content_commit": str,
    "manager_version": str,
    "last_run": str,
    "last_backup": str,
    "last_run_record": str,
    "result": str,
    "enrolled": bool,
}
# Optional record fields. The machine ID is a record's identity; the hostname is its display name.
# The distro and the host name are displayed facts, never keys. Two WSL distros share a host name.
OPTIONAL_FIELDS = {
    "ssh_target": str,
    "recorded_at": str,
    "machine_id": str,
    "aliases": list,
    "distro": str,
    "system_hostname": str,
}
MACHINE_ID = ".fleet/machine-id"
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
ALIAS_LIMIT = 8


def load(home):
    path = home / REGISTRY
    if path.is_symlink():
        raise ValueError(
            "Fleet registry is a symlink. Keep registry.toml inside the manager store."
        )
    data = read_toml(path) if path.exists() else {"schema_version": 1, "machines": {}}
    if (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("machines"), dict)
    ):
        raise ValueError("Fleet registry needs schema_version = 1 and a machines table.")
    removed = data.setdefault("removed", {})
    if not isinstance(removed, dict) or any(
        not isinstance(name, str) or not isinstance(when, str) for name, when in removed.items()
    ):
        raise ValueError("Fleet registry removal markers must map machine names to times.")
    for key, record in data["machines"].items():
        if not isinstance(key, str) or not key or not isinstance(record, dict):
            raise ValueError("Each fleet record needs a nonempty machine key and a table.")
        if key not in (record.get("hostname"), collision_key(record)):
            raise ValueError("Fleet record keys must match their hostname fields.")
        for name, kind in FIELDS.items():
            if name not in record or type(record[name]) is not kind:
                raise ValueError(f"Fleet record {key}: {name} must be {kind.__name__}.")
        for name, kind in OPTIONAL_FIELDS.items():
            if name in record and type(record[name]) is not kind:
                raise ValueError(f"Fleet record {key}: {name} must be {kind.__name__}.")
        if record.get("machine_id") and not IDENTIFIER.fullmatch(record["machine_id"]):
            raise ValueError(f"Fleet record {key}: machine_id must be 32 hexadecimal digits.")
        if any(
            not isinstance(alias, str) or not NAME.fullmatch(alias)
            for alias in record.get("aliases", [])
        ):
            raise ValueError(f"Fleet record {key}: aliases must be machine names.")
        for name in ("home", "last_backup", "last_run_record"):
            if record[name] and not Path(record[name]).is_absolute():
                raise ValueError(f"Fleet record {key}: {name} must be an absolute path.")
        if key == machine_name() and record["home"] == str(home):
            for name in ("last_backup", "last_run_record"):
                if record[name]:
                    record[name] = str(relocated(record[name], home))
    return data


def machine_name():
    """Return this machine's fleet name. The run index owns the rule; it runs before the manager."""
    return run_index.machine_name()


def machine_id(home, create=False):
    """Return this machine's stable ID from its own store. Create it once when asked.

    The ID identifies the machine in every record and exchange. The name only labels it.
    """
    path = home / MACHINE_ID
    if path.is_symlink():
        raise ValueError("The machine ID is a symlink. Keep machine-id inside the manager store.")
    if path.is_file():
        saved = path.read_text().strip()
        if IDENTIFIER.fullmatch(saved):
            return saved
    if not create:
        return ""
    created = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic(path, created + "\n")
    return created


def collision_key(record):
    """Return the key for a member whose hostname another machine ID already uses."""
    return f"{record['hostname']}~{record['machine_id'][:8]}" if record.get("machine_id") else None


def resolve(data, word):
    """Return the record key that a word names: its key, its hostname, an alias, or its SSH target.

    A word that matches several members is ambiguous and stops the command.
    """
    machines = data["machines"]
    if word in machines:
        return word
    for fields in (("hostname",), ("aliases",), ("ssh_target",)):
        found = sorted(
            key
            for key, record in machines.items()
            for field in fields
            if word == record.get(field)
            or field == "aliases"
            and word in record.get(field, [])
            # A sibling answers to its distro name as well as to its wsl: target.
            or field == "ssh_target"
            and record.get(field) == "wsl:" + word
        )
        if len(found) > 1:
            raise ValueError(f"{word} matches several members: {', '.join(found)}. Use one key.")
        if found:
            return found[0]
    return None


def os_name():
    system = platform.system()
    if system == "Linux" and (
        os.environ.get("WSL_DISTRO_NAME")
        or Path("/proc/version").exists()
        and "microsoft" in Path("/proc/version").read_text().lower()
    ):
        return "WSL"
    return "macOS" if system == "Darwin" else system


def machine(home, manager_version, fallback=None):
    profile_path = home / ".fleet/profile.toml"
    profile = read_toml(profile_path) if profile_path.exists() else fallback or {}
    checkout = dotfiles_library.active(home)
    commit = (
        run("git", "-C", str(checkout), "rev-parse", "HEAD").strip()
        if (checkout / ".git").exists()
        else ""
    )
    return {
        "hostname": machine_name(),
        "home": str(home),
        "os": os_name(),
        "domain": profile.get("domain", ""),
        "domain_chosen": profile.get("domain_chosen", False),
        "content_repo": profile.get("content_repo", ""),
        "content_commit": commit,
        "manager_version": manager_version,
        "last_run": "",
        "last_backup": "",
        "last_run_record": "",
        "result": "not recorded",
        "enrolled": profile_path.exists(),
        "ssh_target": "",
        "machine_id": machine_id(home),
        "distro": os.environ.get("WSL_DISTRO_NAME", ""),
        "system_hostname": socket.gethostname(),
    }


def save_name(home, name):
    """Save the name chosen at enrollment. Never rename: a different saved name stops this."""
    if not NAME.fullmatch(name or ""):
        raise ValueError("A machine name uses letters, digits, dots, hyphens, and underscores.")
    saved = run_index.saved_name(home)
    if (home / ".fleet/profile.toml").exists():
        # An enrollment from before saved names is named by the name it reports now.
        current = saved or run_index.machine_name()
        if name != current:
            raise ValueError(f"This machine is already named {current}. Fleet never renames it.")
    # A first enrollment that stopped before its profile may choose again.
    if saved != name:
        path = home / run_index.NAME_FILE
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic(path, name + "\n")
    return name


def sanitized(text):
    """Lowercase TEXT. Spaces and underscores become hyphens; other symbols go."""
    text = re.sub(r"[\s_]+", "-", text.strip().lower())
    return re.sub(r"[^a-z0-9.-]", "", text).strip("-.")


# A Windows default host name starts with one of these words, so its first part says nothing.
GENERIC_HOSTS = ("desktop", "laptop", "pc", "win")


def taken_names(home):
    """Return the names that other machine IDs use in this machine's registry."""
    own = machine_id(home)
    try:
        machines = load(home)["machines"]
    except (OSError, ValueError):
        return set()
    return {
        key
        for key, record in machines.items()
        if record.get("machine_id") and record.get("machine_id") != own
    }


def release_facts(release=Path("/etc/os-release")):
    """Read ID and VERSION_ID from os-release. A missing file gives no facts."""
    facts = {}
    try:
        for line in release.read_text().splitlines():
            key, _, item = line.partition("=")
            facts[key] = item.strip().strip('"')
    except OSError:
        pass
    return facts


def proposed_name(
    distro=None, release=Path("/etc/os-release"), hostname=None, home=None, system=None
):
    """Propose a machine name from its host and platform. The user may edit it at the prompt.

    WSL: host, distro and version, then scope, such as dell-ubuntu-26.04-alertventure.
    The scope is the WSL distro's own name unless it equals the base distro and version.
    Linux: host and distro, such as dell-fedora. macOS: host-macos. Windows: host-windows.
    A release number that names another release of this distro is removed first.
    So LAPTOP-DEBIAN-11 on Debian 12 gives laptop-debian-debian, and WSL Ubuntu-24.04 on 26.04
    gives dell-ubuntu-26.04.
    A name that another machine ID uses gets -2, then -3, until it is free.
    A suffix that names another release of this distro is skipped.
    """
    distro = os.environ.get("WSL_DISTRO_NAME", "") if distro is None else distro
    hostname = socket.gethostname() if hostname is None else hostname
    if system is None:
        system = "WSL" if distro else {"Darwin": "macOS"}.get(platform.system(), platform.system())
    facts = release_facts(release) if system in ("WSL", "Linux") else {}
    host = sanitized(re.split(r"[-.]", hostname)[0])
    if host in GENERIC_HOSTS:
        host = sanitized(hostname)
    if system == "WSL":
        base = "-".join(
            part
            for part in (sanitized(facts.get("ID", "")), sanitized(facts.get("VERSION_ID", "")))
            if part
        )
        scope = sanitized(distro)
        # An upgraded WSL distro keeps its old name, such as Ubuntu-24.04 on 26.04.
        # Drop that old release from the scope.
        current = without_other_releases(scope, release)
        # A scope that held only the distro and its old release then says nothing more.
        if current != scope and current == sanitized(facts.get("ID", "")):
            current = ""
        parts = (host, base, "" if current == base else current)
    elif system == "Linux":
        parts = (host, sanitized(facts.get("ID", "")))
    else:
        parts = (host, sanitized(system))
    name = "-".join(part for part in parts if part) or sanitized(hostname)
    # A host name can hold another release too, such as LAPTOP-DEBIAN-11 on Debian 12.
    name = without_other_releases(name, release)
    reason = version_conflict(name, release)
    if reason:
        raise ValueError("Fleet proposed a machine name that its own rule rejects: " + reason)
    taken = taken_names(home) if home is not None else set()
    candidate, number = name, 2
    # Skip a suffix that reads as another release, such as -10 on Debian 12.
    # Only a suffix with as many digits as a one-part version can do that.
    # The loop reaches one only after many taken names, so this limit is never too low.
    limit = 10 * (len(taken) + 2) + 100
    while candidate in taken or version_conflict(candidate, release):
        if number > limit:
            raise ValueError(
                f"Fleet found no free machine name after {limit} tries."
                " Choose one with --machine-name NAME."
            )
        candidate, number = f"{name}-{number}", number + 1
    return candidate


def without_other_releases(name, release=Path("/etc/os-release")):
    """Remove each release number in NAME that version_conflict() would reject.

    The distro ID stays, so laptop-debian-11 becomes laptop-debian on Debian 12.
    """
    facts = release_facts(release)
    distro, version = sanitized(facts.get("ID", "")), facts.get("VERSION_ID", "")
    if not distro or not version:
        return name

    def keep_or_drop(found):
        number = found.group(3)
        if reads_as_version(number, version) and not same_release(number, version):
            return found.group(1) + found.group(2)
        return found.group(0)

    pattern = rf"(^|-)({re.escape(distro)})-(\d+(?:\.\d+)*)(?=-|$)"
    # Removing one release can expose the next, as in debian-11-10. Repeat until nothing changes.
    previous, name = None, name.lower()
    while name != previous:
        previous, name = name, re.sub(pattern, keep_or_drop, name)
    return name


def reads_as_version(number, version):
    """Return True when NUMBER after the distro ID is a release, not a taken-name suffix.

    A suffix such as -2 is one undotted number, so a dotted number is always a release.
    On a one-part version, a number with as many digits is a release too.
    So 40 is a release on Fedora 41, and 20230101 is one on Tumbleweed 20240101.
    A number with another digit count, such as 2, is a suffix.
    """
    if "." in number:
        return True
    return "." not in version and len(number) == len(version)


def same_release(number, version):
    """Return True when one version is the other, or extends it by more parts.

    So 24.04.1 names Ubuntu 24.04, and 3.20 names Alpine 3.20.3.
    Each dot-separated part must match exactly, so 3.2 does not name Alpine 3.20.3.
    """
    numbers, versions = number.split("."), version.split(".")
    shared = min(len(numbers), len(versions))
    return numbers[:shared] == versions[:shared]


def version_conflict(name, release=Path("/etc/os-release")):
    """Return a reason when NAME holds this distro's ID with another version, else ""."""
    facts = release_facts(release)
    distro, version = sanitized(facts.get("ID", "")), facts.get("VERSION_ID", "")
    if not distro or not version:
        return ""
    for found in re.finditer(rf"(?:^|-){re.escape(distro)}-(\d+(?:\.\d+)*)(?=-|$)", name.lower()):
        number = found.group(1)
        if reads_as_version(number, version) and not same_release(number, version):
            return (
                f"{name} names {distro} {number}, but this machine runs"
                f" {distro} {version}. Choose another name."
            )
    return ""


def backfill(home):
    """Save the current name and facts of an existing enrollment. Change nothing else.

    An enrollment from before names were chosen keeps its name: the name it reports now.
    """
    if not (home / ".fleet/profile.toml").exists():
        return
    if not run_index.saved_name(home) and not os.environ.get("FLEET_MACHINE_NAME"):
        save_name(home, machine_name())
    path = home / REGISTRY
    if not path.exists():
        return
    facts = {
        "distro": os.environ.get("WSL_DISTRO_NAME", ""),
        "system_hostname": socket.gethostname(),
    }
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(home)
        record = data["machines"].get(machine_name())
        changed = {
            key: item
            for key, item in facts.items()
            if item and record and record.get(key, "") != item
        }
        if not changed:
            return
        record.update(changed)
        atomic(path, serialize(data))


def serialize(data):
    lines = [
        "# Recorded machine facts. This file is not a live network view.",
        "schema_version = 1",
        "",
    ]
    for key, record in sorted(data["machines"].items()):
        lines.append("[machines." + value(key) + "]")
        lines += [value(name) + " = " + value(item) for name, item in record.items()]
        lines.append("")
    if data.get("removed"):
        # A removal marker keeps a removed machine from returning through an older peer list.
        lines.append("[removed]")
        lines += [
            value(name) + " = " + value(when) for name, when in sorted(data["removed"].items())
        ]
        lines.append("")
    return "\n".join(lines)


def refresh(home, manager_version, backup, result, fallback=None, *, run_record=None):
    """Replace this machine's record under a lock; preserve all other records."""
    path = home / REGISTRY
    if not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError("Fleet registry parent leaves HOME. Keep the manager store inside HOME.")
    if not run_index.saved_name(home) and not os.environ.get("FLEET_MACHINE_NAME"):
        # The first recorded run saves the name, so a later host name change renames nothing.
        save_name(home, machine_name())
    current = machine(home, manager_version, fallback)
    current.update(
        last_run=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        last_backup=str(backup) if backup else "",
        last_run_record=str(run_record or backup / "RUN.json"),
        result=result,
        machine_id=machine_id(home, create=True),
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # No saved credentials or persistent network service are involved.
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(home)
        settle_own(data, current)
        atomic(path, serialize(data))
    return current


def same_result(record, other):
    """Two records share a last result: the same run time and the same content commit."""
    return (
        bool(record.get("last_run"))
        and record.get("last_run") == other.get("last_run")
        and bool(record.get("content_commit"))
        and record.get("content_commit") == other.get("content_commit")
    )


def remember_aliases(record, names):
    """Keep the names a member was known by, newest last, without its current name."""
    aliases = [alias for alias in record.get("aliases", []) + names if alias != record["hostname"]]
    aliases = list(dict.fromkeys(reversed(aliases)))[:ALIAS_LIMIT][::-1]
    if aliases:
        record["aliases"] = aliases
    else:
        record.pop("aliases", None)


def settle_own(data, current):
    """Save this machine's record under its own name. Absorb records that are this machine.

    A record with this machine's ID is this machine under an older name. A record from before IDs
    is this machine when it carries this machine's own last result: run time and content commit.
    """
    machines, own = data["machines"], current["hostname"]
    previous = machines.get(own)
    if previous is not None and previous.get("machine_id", "") not in ("", current["machine_id"]):
        # Another machine reported this name first. It keeps listing, under its collision key.
        machines[collision_key(previous)] = machines.pop(own)
        previous = None
    renamed = [
        key
        for key, record in machines.items()
        if key != own and record.get("machine_id") == current["machine_id"]
    ]
    if previous is None and renamed:
        previous = machines[renamed[0]]
    absorbed = list(renamed)
    if previous is not None:
        absorbed += [
            key
            for key, record in machines.items()
            if key != own and not record.get("machine_id") and same_result(record, previous)
        ]
    names = [machines.pop(key)["hostname"] for key in dict.fromkeys(absorbed)]
    current["ssh_target"] = (previous or {}).get("ssh_target", "")
    current["aliases"] = list((previous or {}).get("aliases", []))
    remember_aliases(current, names)
    machines[own] = current


# Older members send these digest fields only. Members before 0.8.1 also omit the version.
LEGACY_DIGEST_FIELDS = (
    "hostname",
    "ssh_target",
    "domain",
    "os",
    "enrolled",
    "manager_version",
    "recorded_at",
)
# The exchange carries the complete record. The SSH target stays with each viewer.
# The distro and the host name stay local facts. A viewer learns them when it meets a sibling.
EXCHANGED = (*FIELDS, "machine_id", "aliases", "recorded_at")
LOCAL_FACTS = ("distro", "system_hostname")


def fact_time(record):
    """Return the time of a record's facts: when they were recorded, or its last run."""
    return record.get("recorded_at") or record.get("last_run") or ""


def digest(home):
    """Describe fleet members and removal markers for an exchange on contact.

    Each member carries its complete record except the SSH target. How a viewer reaches a
    machine is that viewer's own fact.
    """
    data = load(home)
    members = []
    for _, record in sorted(data["machines"].items()):
        if not record["enrolled"]:
            continue
        compact = {field: record[field] for field in EXCHANGED if field in record}
        compact["recorded_at"] = fact_time(record)
        members.append(compact)
    return {"members": members, "removed": dict(sorted(data["removed"].items()))}


def blank(name):
    record = {field: False if kind is bool else "" for field, kind in FIELDS.items()}
    record.update(hostname=name, result="not recorded")
    return record


def marker_time(removed, record):
    """Return the newest removal marker that names this record by ID or by name."""
    names = [record["hostname"], record.get("machine_id", "")]
    return max(removed.get(name, "") for name in names if name)


def find_key(machines, fact):
    """Find the local record of a member: by its ID, else a record from before IDs by its name."""
    found = fact.get("machine_id", "")
    if found:
        for key, record in machines.items():
            if record.get("machine_id") == found:
                return key
    record = machines.get(fact["hostname"])
    if record is not None and (not found or not record.get("machine_id")):
        return fact["hostname"]
    return None


def absorb(machines, fact, key, own, changes):
    """Fold records from before IDs that name this member into one record. Return its key.

    Candidates are the member's hostname and its aliases. A saved SSH target moves to the survivor.
    """
    if not fact.get("machine_id"):
        return key
    for name in [fact["hostname"], *fact.get("aliases", [])]:
        record = machines.get(name)
        if name in (key, own) or record is None or record.get("machine_id"):
            continue
        if key is None:
            key = name
            continue
        if record.get("ssh_target") and not machines[key].get("ssh_target"):
            machines[key]["ssh_target"] = record["ssh_target"]
        del machines[name]
        changes["dropped"].append(name)
    return key


def merge(data, received, own, own_id="", contacted=None, answered=None):
    """Merge a peer digest into registry data. Return the names that were added, changed, or dropped.

    Records match by machine ID; the hostname is a display name. The union of members wins. The
    newest record time wins each field. A removal marker beats a member fact recorded at or before
    the removal. A machine stays the only authority on itself. Contacted names the record whose
    connection reached the answering machine; answered is that machine's own ID and name.
    """
    changes = {"added": [], "updated": [], "dropped": []}
    machines = data["machines"]
    removed = data.setdefault("removed", {})
    for name, when in received.get("removed", {}).items():
        if name not in (own, own_id) and when > removed.get(name, ""):
            removed[name] = when
    answered = answered or {}
    if (
        contacted in machines
        and contacted != own
        and IDENTIFIER.fullmatch(str(answered.get("machine_id", "")))
        and not machines[contacted].get("machine_id")
    ):
        # The machine that answered at this record's connection is that record's machine.
        machines[contacted]["machine_id"] = answered["machine_id"]
    mine = machines.get(own, {})
    for fact in received.get("members", []):
        name, found = fact["hostname"], fact.get("machine_id", "")
        if found and found == own_id or not found and name == own:
            for alias in fact.get("aliases", []):
                record = machines.get(alias)
                if alias != own and record is not None and not record.get("machine_id"):
                    del machines[alias]  # That record was this machine under an older name.
                    changes["dropped"].append(alias)
            continue
        if not found and same_result(fact, mine):
            # A record from before IDs that carries this machine's own last result is this machine.
            if name in machines and not machines[name].get("machine_id"):
                del machines[name]
                changes["dropped"].append(name)
            continue
        if fact["recorded_at"] <= marker_time(removed, fact):
            continue
        before = len(changes["dropped"])
        key = absorb(machines, fact, find_key(machines, fact), own, changes)
        if key is None:
            record, key = blank(name), None
        else:
            record = machines[key]
            bound = bool(found) and not record.get("machine_id")
            if bound:
                record["machine_id"] = found  # A record from before IDs takes the member's ID.
            if fact["recorded_at"] <= fact_time(record) and len(changes["dropped"]) == before:
                if bound:
                    changes["updated"].append(key)
                continue
        saved_target = record.get("ssh_target", "")
        old_name = record["hostname"]
        record.update({field: fact[field] for field in EXCHANGED if field in fact})
        record["ssh_target"] = saved_target  # An empty or foreign target never replaces this one.
        holder = machines.get(name)
        target = name
        if name == own or holder is not None and holder is not record:
            target = collision_key(record) or name
        if key is not None and key != target:
            del machines[key]
            remember_aliases(record, [old_name])
        machines[target] = record
        for marker in (name, found):
            removed.pop(marker, None)
        if key is None:
            changes["added"].append(target)
        else:
            changes["updated"].append(target if key == target else f"{target} (was {key})")
    for key, record in list(machines.items()):
        if key == own or own_id and record.get("machine_id") == own_id:
            continue
        when = max(marker_time(removed, record), removed.get(key, ""))
        if when and fact_time(record) <= when:
            del machines[key]
            changes["dropped"].append(key)
    return changes


def merge_into(home, received, contacted=None, answered=None):
    """Merge a digest into this machine's registry under the shared lock. No backup is taken."""
    path = home / REGISTRY
    own, own_id = machine_name(), machine_id(home)
    # A contact that brings nothing new writes nothing, not even the lock file.
    if not any(merge(load(home), received, own, own_id, contacted, answered).values()):
        return {"added": [], "updated": [], "dropped": []}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(home)
        changes = merge(data, received, own, own_id, contacted, answered)
        if any(changes.values()):
            atomic(path, serialize(data))
    return changes


def sync_own_version(home, manager_version):
    """Keep this machine's own record at its installed manager's version. Return True on a change.

    Enrollment writes the version once. A republished manager, or any contact, rechecks it here.
    The change gets a new record time, so peers take it on the next digest exchange.
    """
    path = home / REGISTRY
    if not path.is_file():
        return False
    own = machine_name()
    record = load(home)["machines"].get(own)
    if record is None or record.get("manager_version") == manager_version:
        return False
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(home)
        record = data["machines"].get(own)
        if record is None or record.get("manager_version") == manager_version:
            return False
        record["manager_version"] = manager_version
        record["recorded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        )
        atomic(path, serialize(data))
    return True


def mark_removed(data, name, when=None):
    """Record that a machine left the fleet, and drop its record. The marker names its ID."""
    when = when or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    record = data["machines"].pop(name, None) or {}
    data.setdefault("removed", {})[record.get("machine_id") or name] = when


def duplicate_notes(members):
    """Name members that may be one machine: a shared name, or one last run with no ID to tell."""
    notes = {key: [] for key in members}
    for key, record in members.items():
        for other, peer in members.items():
            if other != key and record["hostname"] == peer["hostname"]:
                notes[key].append(
                    f"Name collision: {other} also reports {record['hostname']} with another ID."
                )

    def one_run(record, peer):
        return bool(peer.get("last_run")) and peer["last_run"] in (
            record.get("last_run"),
            record.get("recorded_at"),
        )

    names = sorted(members)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            a, b = members[first], members[second]
            if a.get("machine_id") and b.get("machine_id") or a["hostname"] == b["hostname"]:
                continue
            if one_run(a, b) or one_run(b, a):
                notes[second].append(
                    f"Possible duplicate of {first}: same last run. The next contact merges them "
                    "once both machines run this Fleet version."
                )
    return notes


def entities(home, manager_version):
    """List known facts as one compact block per machine, plus a one-line row for larger fleets."""
    data = load(home)
    local = machine_name()
    if local not in data["machines"]:
        data["machines"][local] = machine(home, manager_version)
    output = []
    # The list holds fleet members. Connection-only records stay data for remote commands.
    members = {
        key: record
        for key, record in data["machines"].items()
        if record["enrolled"] or key == local
    }
    duplicates = duplicate_notes(members)
    for key, record in sorted(members.items(), key=lambda item: (item[0] != local, item[0])):
        known = any(
            record.get(field)
            for field in ("home", "os", "domain", "content_repo", "manager_version", "last_run")
        )
        target = record.get("ssh_target")
        fields = [
            ("Connection", "SSH uses this name" if target == key else target or "no connection"),
            (
                "Enrollment",
                "enrolled" if record["enrolled"] else "not enrolled" if known else "not recorded",
            ),
            ("OS", record.get("os")),
            ("Distro", record.get("distro")),
            # The host name is a fact. Two WSL distros on one host report the same one.
            (
                "Hostname",
                record.get("system_hostname")
                if record.get("system_hostname") not in ("", None, key)
                else None,
            ),
            (
                "Domain",
                (record["domain"] + (" · chosen" if record["domain_chosen"] else " · UNCHOSEN"))
                if record["domain"]
                else None,
            ),
            ("Content", record.get("content_repo")),
            ("Commit", record.get("content_commit")),
            ("Fleet", record.get("manager_version")),
        ]
        if record["result"] and record["result"] != "not recorded":
            fields.append(
                (
                    "Last result",
                    record["result"]
                    + (" · recorded " + record["last_run"] if record["last_run"] else ""),
                )
            )
        name = key + (" · this machine" if key == local else " · recorded only")
        summary = [
            "this machine" if key == local else None,
            record.get("os"),
            record.get("distro"),
            (record["domain"] + ("" if record["domain_chosen"] else " UNCHOSEN"))
            if record["domain"]
            else None,
            "Fleet " + record["manager_version"] if record.get("manager_version") else None,
            (target if target.startswith("wsl:") else "ssh " + target)
            if target
            else "no connection",
            (record["result"] + " " + record["last_run"][:10]).strip()
            if record["result"] and record["result"] != "not recorded"
            else None,
        ]
        output.append(
            {
                "name": name,
                "row": (key, " · ".join(part for part in summary if part)),
                "fields": fields,
                "notes": ([] if known else ["No local data recorded for this machine."])
                + duplicates[key],
            }
        )
    return output


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    from style import ArgumentParser

    argument_parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from pathlib import Path
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
