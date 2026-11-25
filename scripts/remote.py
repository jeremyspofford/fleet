"""Run a validated Fleet action through the target's own installed manager.

Usage: dotfleet status MACHINE; dotfleet machines set-ssh --target mini-pc.
SSH reads the user's connection settings and keys. Requests travel through stdin,
never through the remote shell command. No files or profiles cross machines.
Help: python3 scripts/remote.py --help.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import registry
import connections
import topology
import remote_history
import style
import run_index
from render_run import render_view

CAPABLE = ("status", "check", "diff", "doctor", "update")
# Profile commands run on the target. The target's manager is the only writer of its profile.
PROFILE_ACTIONS = {
    "agents": {"list": (), "set": ("name", "state")},
    "domain": {"list": (), "set": ("name",)},
    "dotfiles": {"source": ("repo",)},
    "groups": {"list": (), "set": ("name", "state")},
    "tools": {
        "list": (),
        "set": ("tool", "version"),
        "remove": ("tool",),
        "update": ("tool",),
        "adopt": ("tool",),
    },
}
# A request may leave these values out. Without a tool, update and adopt cover every tool.
OPTIONAL = {("tools", "update"): {"tool"}, ("tools", "adopt"): {"tool"}}
PATTERNS = {
    ("agents", "name"): r"(?:skills|rules|mcp|connectors|plugins|hooks)\.[a-z0-9]+(?:-[a-z0-9]+)*",
    ("agents", "state"): r"on|off|local",
    ("domain", "name"): r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*",
    ("groups", "name"): r"[A-Za-z0-9][A-Za-z0-9_.-]*",
    ("groups", "state"): r"on|off",
    ("tools", "tool"): r"[A-Za-z0-9][A-Za-z0-9:._@/-]*",
    ("tools", "version"): r"[A-Za-z0-9][A-Za-z0-9._+-]*",
    # A Git address: https, ssh, scp-style git@, or an absolute local path. No shell text.
    ("dotfiles", "repo"): r"(?:https://|ssh://|git@)[A-Za-z0-9._~@:/+-]+|/[A-Za-z0-9._~/+-]+",
}
CONTEXT = "FLEET_REMOTE_RUN"
REASONS = {
    "configure": "configure needs a terminal on that machine",
    "rollback": "rollback is destructive and must run on that machine",
    "uninstall": "uninstall is destructive and must run on that machine",
}
# This literal is the entire remote command. User input only enters the JSON request.
REMOTE_COMMAND = 'export PATH="$HOME/.local/bin:$PATH"; exec "$HOME/.local/bin/dotfleet" _remote'
PROBE_COMMAND = 'test -x "$HOME/.local/bin/dotfleet"'


def validate_target(target):
    if not isinstance(target, str) or not re.fullmatch(
        r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.:-]*", target
    ):
        raise ValueError("SSH target must be a host name or user@host, without spaces or options.")
    return target


def validate_profile_request(request):
    """Accept one profile command with named values. Nothing else crosses the connection."""
    if set(request) != {"version", "command", "action", "arguments"}:
        raise ValueError("Invalid remote request. Update Fleet on both machines.")
    command, action, arguments = request["command"], request["action"], request["arguments"]
    if command not in PROFILE_ACTIONS or action not in PROFILE_ACTIONS.get(command, {}):
        raise ValueError(
            "Only domain, groups, agents, tools, and dotfiles source choices can change remotely."
        )
    expected = set(PROFILE_ACTIONS[command][action])
    required = expected - OPTIONAL.get((command, action), set())
    if not isinstance(arguments, dict) or not required <= set(arguments) <= expected:
        raise ValueError(f"Invalid values for remote {command} {action}.")
    for key, value in arguments.items():
        if not isinstance(value, str) or not re.fullmatch(PATTERNS[(command, key)], value):
            raise ValueError(f"Invalid {key} for remote {command} {action}.")
    return request


MACHINE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def validate_deploy_request(request):
    """Accept one deploy step: facts, join with the caller's record, or a reverse test."""
    import registry

    action = request.get("action")
    expected = {
        "facts": set(),
        "join": {"peer"},
        "test-return": {"name"},
        "exchange": {"digest"},
        "unenroll": set(),
    }
    if action not in expected or set(request) != {"version", "action", *expected[action]}:
        raise ValueError("Invalid deploy request. Update Fleet on both machines.")
    if action == "test-return" and not (
        isinstance(request["name"], str) and MACHINE_NAME.fullmatch(request["name"])
    ):
        raise ValueError("Invalid machine name in the deploy request.")
    if action == "exchange":
        validate_digest(request["digest"])
    if action == "join":
        peer = request["peer"]
        allowed = set(registry.FIELDS) | set(registry.OPTIONAL_FIELDS)
        if not isinstance(peer, dict) or set(peer) - allowed or set(registry.FIELDS) - set(peer):
            raise ValueError("Invalid machine record in the deploy request.")
        for key, kind in registry.FIELDS.items():
            if type(peer[key]) is not kind:
                raise ValueError(f"Invalid {key} in the deploy request.")
        if not MACHINE_NAME.fullmatch(peer["hostname"]):
            raise ValueError("Invalid machine name in the deploy request.")
        try:
            validate_member(dict(peer, recorded_at=peer.get("recorded_at", "")))
        except ValueError:
            raise ValueError("Invalid machine record in the deploy request.") from None
    return request


def validate_member(fact):
    """Accept one member record: the complete form, or the short form older members send."""
    import registry

    fields = {**registry.FIELDS, **registry.OPTIONAL_FIELDS}
    if not isinstance(fact, dict) or not {"hostname", "enrolled", "recorded_at"} <= set(fact):
        raise ValueError("Invalid member in the registry digest.")
    # A newer member may send a field this version does not know. Drop it; store nothing unknown.
    for unknown in set(fact) - set(fields):
        del fact[unknown]
    for field, value in fact.items():
        if type(value) is not fields[field]:
            raise ValueError("Invalid member in the registry digest.")
        if isinstance(value, str) and len(value) > 400:
            raise ValueError("Invalid member in the registry digest.")
    if not MACHINE_NAME.fullmatch(fact["hostname"]):
        raise ValueError("Invalid member in the registry digest.")
    if fact.get("machine_id") and not registry.IDENTIFIER.fullmatch(fact["machine_id"]):
        raise ValueError("Invalid member in the registry digest.")
    aliases = fact.get("aliases", [])
    if len(aliases) > registry.ALIAS_LIMIT or any(
        not isinstance(alias, str) or not MACHINE_NAME.fullmatch(alias) for alias in aliases
    ):
        raise ValueError("Invalid member in the registry digest.")
    for field in ("home", "last_backup", "last_run_record"):
        if fact.get(field) and not fact[field].startswith("/"):
            raise ValueError("Invalid member in the registry digest.")
    if fact.get("ssh_target"):
        validate_target(fact["ssh_target"])
    return fact


def validate_digest(digest):
    """Accept member records and removal markers. Every value is a short, typed fact."""
    if not isinstance(digest, dict) or set(digest) != {"members", "removed"}:
        raise ValueError("Invalid registry digest.")
    if not isinstance(digest["members"], list) or not isinstance(digest["removed"], dict):
        raise ValueError("Invalid registry digest.")
    for fact in digest["members"]:
        validate_member(fact)
    for name, when in digest["removed"].items():
        if not MACHINE_NAME.fullmatch(name) or not isinstance(when, str) or len(when) > 40:
            raise ValueError("Invalid removal marker in the registry digest.")
    return digest


def validate_request(request):
    version = request.get("version") if isinstance(request, dict) else None
    if type(version) is int and version == 3:
        return validate_deploy_request(request)
    if type(version) is int and version == 2:
        return validate_profile_request(request)
    if not isinstance(request, dict) or set(request) != {"version", "action", "check", "stat"}:
        raise ValueError("Invalid remote request. Update Fleet on both machines.")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ValueError("Unsupported remote request version. Update Fleet on both machines.")
    action = request["action"]
    if not isinstance(action, str) or action not in CAPABLE:
        raise ValueError("Only status, check, diff, doctor, and update can run remotely.")
    for option, allowed in (("check", "status"), ("stat", "diff")):
        if type(request[option]) is not bool or request[option] and action != allowed:
            raise ValueError("Invalid remote option for " + action + ".")
    if request["check"]:
        raise ValueError("Status never fetches. Run check on that machine instead.")
    return request


def refusal(machine, action):
    reason = REASONS.get(action, "this command has no remote operation")
    print(f"Error: {machine}: remote {action} refused; {reason}.")
    return 2


def test_connection(home, name, target):
    try:
        result = connections.invoke(target, "true", probe=True)
        if result.returncode:
            raise ConnectionError(
                result.stderr or result.stdout or f"SSH test failed (exit {result.returncode})"
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        diagnostics(home, name, target, "connection test", "connection", error)
        reason = connections.report_failure(home, name, target, error)
        topology.remember(home, name, target, "connection test", reason, 2)
        print("Saved connection kept. Retry after fixing access or waking the target.")
        return 2
    kind = "wsl.exe" if connections.is_sibling(target) else "SSH"
    print(f"PASS: {name}: {kind} connection test passed.")
    topology.remember(home, name, target, "connection test", f"{kind} test passed", 0)
    return 0


def diagnostics(home, machine, target, action, phase, result):
    try:
        remote_history.save(home, machine, target, action, phase, result)
    except (OSError, ValueError):
        # Never expose raw shell output if the private record cannot be written.
        pass


def missing_command(home, machine, target, action, report=None):
    reason = "Fleet is not installed on this machine (or predates the fleet command)"
    print(f"Error: {machine}: {reason}.")
    print("Next: Run the Fleet one-liner on that machine once, then retry.")
    topology.remember(home, machine, target, action, reason, 2)
    if report is not None:
        report["result"] = "failed: " + reason
    return 2


def profile_request(command, action, arguments):
    return validate_request(
        dict(version=2, command=command, action=action, arguments=dict(arguments))
    )


def execute(home, machine, action, *, check=False, stat=False, request=None, report=None):
    """Run one action on a member. Report, when given, receives a one-line result."""
    report = {} if report is None else report
    if request is None:
        if action not in CAPABLE:
            return refusal(machine, action)
        request = validate_request(dict(version=1, action=action, check=check, stat=stat))
    try:
        data = registry.load(home)
        # A member answers to its name or to the SSH target that this machine uses to reach it.
        machine = registry.resolve(data, machine) or machine
        record = data["machines"].get(machine)
        if record is None:
            raise ValueError("no saved machine; use machines set-ssh --target HOST first")
        if not record.get("ssh_target"):
            raise ValueError("no connection; use machines set-ssh --name NAME --target HOST first")
        target = validate_target(record["ssh_target"])
    except (OSError, ValueError) as error:
        print(f"Error: {machine}: {connections.one_line(str(error))}.")
        report["result"] = "unreachable: " + connections.one_line(str(error))
        return 2
    phase = "command probe"
    result = None
    try:
        result = connections.invoke(target, PROBE_COMMAND, probe=True)
        if result.returncode or result.stderr:
            diagnostics(home, machine, target, action, "command probe", result)
        if result.returncode in (1, 126, 127):
            return missing_command(home, machine, target, action, report)
        if result.returncode == 255:
            reason = connections.report_failure(
                home, machine, target, ConnectionError(result.stderr)
            )
            topology.remember(home, machine, target, action, reason, 2)
            report["result"] = "unreachable: " + reason
            return 2
        phase = "remote command"
        result = connections.invoke(target, REMOTE_COMMAND, json.dumps(request) + "\n")
        expected = (0, 1) if action in ("check", "diff") else (0,)
        if result.returncode not in expected or result.stderr:
            diagnostics(home, machine, target, action, "remote command", result)
        if result.returncode == 127:
            return missing_command(home, machine, target, action, report)
        if result.returncode == 255:
            reason = connections.report_failure(
                home, machine, target, ConnectionError(result.stderr)
            )
            topology.remember(home, machine, target, action, reason, 2)
            report["result"] = "unreachable: " + reason
            return 2
    except (OSError, subprocess.TimeoutExpired) as error:
        diagnostics(home, machine, target, action, phase, error)
        reason = connections.report_failure(home, machine, target, error)
        topology.remember(home, machine, target, action, reason, 2)
        report["result"] = "unreachable: " + reason
        return 2
    print(render_view("Machine · " + machine, [("Connection", target)], str(home)), end="")
    if isinstance(sys.stdout, style.LoggerStream) and action == "diff":
        sys.stdout.diff = True
    # The target uses the shared renderer, with its own home and its own run record.
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    # A sibling distro on this host answers through wsl.exe; its result line says so.
    route = " through wsl.exe" if connections.is_sibling(target) else ""
    if result.returncode == 0:
        state = action + " succeeded"
        print(f"{machine}: {state}{route} (exit 0).")
    elif result.returncode == 1 and (action in ("check", "diff") or check):
        state = "differences found" if action == "diff" else "updates available"
        print(f"{machine}: {state}{route} (exit 1).")
    else:
        state = action + " failed"
        print(f"Error: {machine}: {state}{route} (exit {result.returncode}).")
    topology.remember(home, machine, target, action, state, result.returncode)
    report["result"] = state if result.returncode == 0 else f"{state} (exit {result.returncode})"
    exchange(home, machine, target)
    return result.returncode if result.returncode > 0 else int(result.returncode != 0)


def installed_version():
    """Return the version of the manager running this code."""
    import command

    return (command.ROOT / "VERSION").read_text().strip()


def exchange(home, machine, target):
    """Swap registry digests with a member after contact. Lists converge when machines talk."""
    import registry

    registry.sync_own_version(home, installed_version())
    request = {"version": 3, "action": "exchange", "digest": registry.digest(home)}
    try:
        result = connections.invoke(target, REMOTE_COMMAND, json.dumps(request) + "\n")
        lines = result.stdout.strip().splitlines()
        answer = json.loads(lines[-1]) if result.returncode == 0 and lines else {}
        received = validate_digest(answer["digest"])
        answered = answer.get("machine") if isinstance(answer.get("machine"), dict) else None
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        # An older or unreachable member keeps its list; the next contact tries again.
        return {}
    changes = registry.merge_into(home, received, contacted=machine, answered=answered)
    summary = [f"{kind} {', '.join(names)}" for kind, names in changes.items() if names]
    if summary:
        print(f"Fleet members updated from {machine}: " + "; ".join(summary) + ".")
    return changes


def unenroll(home, machine, target):
    """Dispatch an unenroll to a member. Return 0 only when its manager removed Fleet."""
    request = {"version": 3, "action": "unenroll"}
    try:
        result = connections.invoke(target, REMOTE_COMMAND, json.dumps(request) + "\n")
    except (OSError, subprocess.TimeoutExpired) as error:
        connections.report_failure(home, machine, target, error)
        return 2
    if result.returncode == 255:
        connections.report_failure(home, machine, target, ConnectionError(result.stderr))
        return 2
    if result.stdout:
        print(result.stdout.rsplit("\n", 2)[0])
    lines = result.stdout.strip().splitlines()
    try:
        code = json.loads(lines[-1]).get("code", 1) if lines else 1
    except ValueError:
        code = 1
    if result.returncode or code:
        print(
            f"Error: {machine}: unenroll failed (exit {result.returncode or code}). Nothing was marked removed."
        )
        return 1
    topology.remember(home, machine, target, "uninstall", "uninstall succeeded", 0)
    return 0


def receive():
    """Validate before calling the same local dispatcher. No remote nesting or terminal."""
    import command
    import ui_channel

    try:
        request = validate_request(json.loads(sys.stdin.read(4097)))
        if request["version"] == 2:
            prepare_environment()
            return receive_profile(Path.home(), request)
        if request["version"] == 3:
            prepare_environment()
            return receive_deploy(Path.home(), request)
        os.environ.pop(ui_channel.CHANNEL, None)
        os.environ.update(
            {
                CONTEXT: "1",
                "NO_COLOR": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "SSH_ASKPASS_REQUIRE": "never",
                "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
            }
        )
        home = Path.home()
        if request["action"] == "update":
            return run_index.run(home, "update", lambda: receive_action(home, request))
        return receive_action(home, request)
    except (OSError, ValueError, RuntimeError) as error:
        print("Error: " + str(error))
        return 2


def prepare_environment():
    import ui_channel

    os.environ.pop(ui_channel.CHANNEL, None)
    os.environ.update(
        {
            CONTEXT: "1",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "SSH_ASKPASS_REQUIRE": "never",
            "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
        }
    )


def receive_profile(home, request):
    """List or save one profile choice. The target's next update applies a saved change."""
    import command

    if request["action"] == "list":
        return command.main([request["command"], "list"])
    label = request["command"] + " " + request["action"]
    return run_index.run(home, label, lambda: command.save_remote_choice(home, request))


def answer(data):
    """Print one JSON line for the caller. Human output may come before it."""
    stream = getattr(sys.stdout, "stream", sys.stdout)
    stream.write(json.dumps(data) + "\n")
    stream.flush()
    return 0


def receive_deploy(home, request):
    """Answer one deploy step on the target. The target's manager writes its own registry."""
    import command
    import registry

    own = registry.machine_name(home)
    # Every contact rechecks the version this machine reports about itself.
    registry.sync_own_version(home, installed_version())
    if request["action"] == "exchange":
        changes = registry.merge_into(home, request["digest"])
        # The caller learns which machine answered at its connection: this machine's ID and name.
        identity = {"machine_id": registry.machine_id(home), "hostname": own}
        return answer({"digest": registry.digest(home), "changes": changes, "machine": identity})
    if request["action"] == "unenroll":
        # The caller confirmed. Remove Fleet under the uninstall rules, keeping backups.
        code = run_index.run(
            home, "uninstall", lambda: command.remove_fleet(home, require_word=None)
        )
        return answer({"code": code}) if code == 0 else code
    if request["action"] == "facts":
        version = (command.ROOT / "VERSION").read_text().strip()
        record = registry.load(home)["machines"].get(own) or registry.machine(home, version)
        return answer({"machine": record})
    if request["action"] == "join":
        peer = request["peer"]
        own_id = registry.machine_id(home)
        if peer.get("machine_id") and peer["machine_id"] == own_id:
            raise ValueError("The caller has this machine's own ID. Nothing was saved.")
        if not peer.get("machine_id") and peer["hostname"] == own:
            raise ValueError("The caller has this machine's own name. Nothing was saved.")

        def add(data):
            fact = {key: peer[key] for key in registry.EXCHANGED if key in peer}
            fact["recorded_at"] = peer.get("recorded_at", "")
            registry.merge(data, {"members": [fact], "removed": {}}, own, own_id)
            key = registry.find_key(data["machines"], fact)
            if key is not None:
                # The return target is how this machine reaches the caller: this viewer's own fact.
                data["machines"][key]["ssh_target"] = peer.get("ssh_target", "")
                data["machines"][key]["recorded_at"] = fact["recorded_at"]

        code = run_index.run(home, "machines join", lambda: command.change_registry(home, add))
        return answer({"code": code}) if code == 0 else code
    data = registry.load(home)
    record = data["machines"].get(registry.resolve(data, request["name"]) or request["name"], {})
    target = record.get("ssh_target", "")
    if not target:
        return answer({"code": 2, "reason": "no saved connection to " + request["name"]})
    try:
        result = connections.invoke(target, "true", probe=True)
        if result.returncode:
            raise ConnectionError(result.stderr or f"SSH test failed (exit {result.returncode})")
    except (OSError, subprocess.TimeoutExpired) as error:
        reason, _ = connections.failure(home, request["name"], target, error)
        topology.remember(home, request["name"], target, "connection test", reason, 2)
        return answer({"code": 2, "reason": reason})
    topology.remember(home, request["name"], target, "connection test", "SSH test passed", 0)
    return answer({"code": 0, "reason": "SSH test passed"})


def receive_action(home, request):
    import command

    run_index.stage("profile")
    gaps = command.command_profile_gaps(command.profile(home))
    if gaps:
        run_index.failure("Remote operation needs a complete target profile: " + "; ".join(gaps))
        print("Error: Remote operation needs a complete target profile:")
        for gap in gaps:
            print("- " + gap)
        print("Complete these choices on the target, then retry.")
        return 2
    args = [request["action"]]
    if request["stat"]:
        args.append("--stat")
    return command.main(args)


def usage():
    import argparse

    style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        sys.exit(style.run_cli(usage, Path(__file__).resolve().parents[1]))
