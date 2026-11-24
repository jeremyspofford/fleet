"""Keep the newest 100 private records of change-making attempts.

Usage: import run, stage, failure, and outcome from this module.
The installer uses begin, stage, and finish before the Python manager starts.
A fresh install downloads this file alone, so it imports only the standard library.
Help: python3 scripts/run_index.py --help.
"""

import argparse
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import uuid

STORE = ".fleet/runs"
LIMIT = 100
ENVIRONMENT = "FLEET_RUN_ID"
ACTIVE = None
IDENTIFIER = re.compile(r"\d{8}-\d{6}-\d{6}-[a-f0-9]{12}")


# The name chosen at enrollment. An enrollment from before names were chosen saves its hostname.
NAME_FILE = ".fleet/machine-name"
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


# Completed install stages, so a re-run of the same command resumes after them.
STAGES_FILE = ".fleet/install-stages.json"
INSTALL_STAGES = ("detect", "software", "enrollment", "verify")


def install_stages(home):
    """Return the recorded install stages. A corrupt record warns once and starts over."""
    path = home / STAGES_FILE
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or not isinstance(record.get("stages"), dict):
            raise ValueError("not a stage record")
        return record["stages"]
    except (OSError, ValueError) as error:
        print(f"Warning: Ignoring {path} ({error}); Fleet rebuilds it.", file=sys.stderr)
        return {}


def complete_stage(home, name, facts=None):
    """Record one completed install stage, with its facts, in a private file."""
    stages = install_stages(home)
    stages[name] = dict(
        facts or {},
        completed=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    )
    path = home / STAGES_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"schema_version": 1, "stages": stages}, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return stages


def saved_name(home=None):
    """Return the name saved in this machine's store, or an empty string."""
    path = (Path.home() if home is None else home) / NAME_FILE
    try:
        name = path.read_text().strip() if path.is_file() and not path.is_symlink() else ""
    except OSError:
        return ""
    return name if NAME.fullmatch(name) else ""


def machine_name():
    """Return this machine's fleet name: the saved name, else its host name.

    FLEET_MACHINE_NAME overrides both. It is for tests only: two homes on one host act as two
    machines. A real machine chooses its name at enrollment.
    """
    return os.environ.get("FLEET_MACHINE_NAME") or saved_name() or socket.gethostname()


def folder(home):
    # Before store migration, attempts stay in an older store so migration moves them along.
    for old in (".local/share/fleet", ".local/share/dotfleet"):
        if (home / old).is_dir():
            return home / old / "runs"
    return home / ".fleet/runs"


def private_directory(path, home):
    if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
        raise ValueError("Run records must stay inside the manager store.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextmanager
def locked(path, home):
    private_directory(path, home)
    descriptor = os.open(path / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write(path, record):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".entry-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def files(home):
    root = folder(home)
    if root.is_symlink() or not root.resolve().is_relative_to(home.resolve()):
        raise ValueError("Run records must stay inside the manager store.")
    return sorted(path for path in folder(home).glob("*.json") if IDENTIFIER.fullmatch(path.stem))


def load(path):
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("Invalid run index entry: " + path.name)
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported run index entry: " + path.name)
    return value


def path_for(home, identifier):
    if not IDENTIFIER.fullmatch(identifier):
        raise ValueError("Invalid run entry name.")
    return folder(home) / (identifier + ".json")


def update(home, identifier, **changes):
    root = folder(home)
    with locked(root, home):
        path = path_for(home, identifier)
        # An unusually long attempt may already have fallen outside the newest 100.
        if not path.exists():
            return
        value = load(path)
        value.update(changes)
        write(path, value)


def begin(home, command):
    inherited = os.environ.get(ENVIRONMENT, "")
    if inherited and IDENTIFIER.fullmatch(inherited):
        path = path_for(home, inherited)
        if path.is_file() and load(path)["result"] == "RUNNING":
            return inherited
    now = datetime.datetime.now(datetime.timezone.utc)
    identifier = now.strftime("%Y%m%d-%H%M%S-%f-") + uuid.uuid4().hex[:12]
    value = {
        "schema_version": 1,
        "id": identifier,
        "time": now.isoformat(timespec="microseconds"),
        "command": command,
        "machine": machine_name(),
        "remote": os.environ.get("FLEET_REMOTE_RUN") == "1",
        "result": "RUNNING",
        "stage": "preflight",
        "error": "",
        "backup": None,
        "record_path": None,
    }
    with locked(folder(home), home):
        write(path_for(home, identifier), value)
        for obsolete in files(home)[:-LIMIT]:
            obsolete.unlink()
    return identifier


def one_line(message):
    return " ".join(str(message).split())[:2000]


def finish(home, identifier, code, error=""):
    path = path_for(home, identifier)
    if not path.exists():
        return
    value = load(path)
    changes = {}
    if code:
        changes["result"] = "INTERRUPTED" if code == 130 else "FAILED"
        changes["error"] = value["error"] or one_line(error) or f"Command stopped (exit {code})."
    elif value["result"] == "RUNNING":
        changes.update(result="SUCCESS", stage="complete")
    update(home, identifier, **changes)


def stage(name):
    if ACTIVE:
        update(*ACTIVE, stage=name)


def failure(message):
    if ACTIVE:
        update(*ACTIVE, error=one_line(message))


def outcome(result, error=""):
    if ACTIVE:
        changes = {"result": result}
        if error:
            changes["error"] = one_line(error)
        if result in ("SUCCESS", "NO_CHANGE"):
            changes["stage"] = "complete"
        update(*ACTIVE, **changes)


def attach(record):
    if ACTIVE:
        backup = record.get("locations", {}).get("Backup folder")
        if not record.get("backup_created"):
            backup = None
        record_path = record.get("record_path") or (
            str(Path(backup) / "RUN.json") if backup else None
        )
        update(*ACTIVE, backup=backup, record_path=record_path)


def run(home, command, function):
    """Share one attempt across nested cores and the update installer subprocess."""
    global ACTIVE
    if ACTIVE:
        return function()
    identifier = begin(home, command)
    previous = os.environ.get(ENVIRONMENT)
    ACTIVE = (home, identifier)
    os.environ[ENVIRONMENT] = identifier
    try:
        code = function()
        finish(home, identifier, code or 0)
        return code
    except BaseException as error:
        code = 130 if isinstance(error, KeyboardInterrupt) else 1
        if isinstance(error, SystemExit):
            code = error.code or 0
        finish(home, identifier, code, str(error))
        raise
    finally:
        ACTIVE = None
        if previous is None:
            os.environ.pop(ENVIRONMENT, None)
        else:
            os.environ[ENVIRONMENT] = previous


def entries(home):
    return [load(path) for path in reversed(files(home))]


def prune_diagnostics(home, store):
    """Keep bounded supplementary records without turning queries into run events."""
    root = home / store
    with locked(root, home):
        saved = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        for obsolete in saved[:-LIMIT]:
            shutil.rmtree(obsolete)


def main(argv=None):
    # This module also runs alone during curl enrollment, before the checkout exists.
    try:
        from style import ArgumentParser
    except ModuleNotFoundError:
        ArgumentParser = argparse.ArgumentParser
    parser = ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    subcommands = parser.add_subparsers(dest="action", required=True)
    start = subcommands.add_parser(
        "begin",
        help="start an attempt or join the inherited attempt",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    start.add_argument("--command", required=True, help="operation name, without private arguments")
    progress = subcommands.add_parser(
        "stage", help="record the stage reached", formatter_class=argparse.RawTextHelpFormatter
    )
    progress.add_argument("--name", required=True, help="stage name")
    end = subcommands.add_parser(
        "finish", help="record the exit result", formatter_class=argparse.RawTextHelpFormatter
    )
    end.add_argument("--code", required=True, type=int, help="process exit code")
    end.add_argument("--error", default="", help="one error line, if the operation failed")
    args = parser.parse_args(argv)
    home = Path.home()
    if args.action == "begin":
        print(begin(home, args.command))
    else:
        identifier = os.environ.get(ENVIRONMENT, "")
        if args.action == "stage":
            update(home, identifier, stage=args.name)
        else:
            finish(home, identifier, args.code, args.error)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print("Error: Could not save the run index: " + str(error), file=sys.stderr)
        sys.exit(1)
