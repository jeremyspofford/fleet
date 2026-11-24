"""Identify machines by a stable ID. A machine's fleet name is the hostname it reports.

The SSH config only finds and reaches machines. The registry merges records by machine ID.
Usage: python3 -m unittest discover -s tests -v. No network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_machine_identity.py --help.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import registry
import run_index
import remote
import test_deploy
from common import atomic

L = "2026-11-19T18:02:11+00:00"
C = "4f1c2d0e9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e"
MAC = "Jeremys-MacBook-Pro.local"
SSH_NAME = "vividcloud-macbook-pro"


def record(name, **facts):
    saved = registry.blank(name)
    saved.update(enrolled=True, os="macOS", domain="work", domain_chosen=True)
    saved.update(facts)
    return saved


def store(home, *records):
    data = {"schema_version": 1, "machines": {}, "removed": {}}
    for saved in records:
        data["machines"][saved["hostname"]] = saved
    atomic(home / registry.REGISTRY, registry.serialize(data))
    return data


class IdentityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)

    def home(self, name):
        path = self.base / name
        (path / ".fleet").mkdir(parents=True)
        return path

    def test_an_exchange_round_trip_preserves_every_carried_field(self):
        mac = self.home("mac")
        own = record(
            MAC,
            home="/Users/owner",
            content_repo="https://example.invalid/content.git",
            content_commit=C,
            manager_version="0.10.1",
            last_run=L,
            last_backup="/Users/owner/.fleet/backups/20261119-180211",
            last_run_record="/Users/owner/.fleet/backups/20261119-180211/RUN.json",
            result="SUCCESS",
            machine_id="a" * 32,
            aliases=[SSH_NAME],
            recorded_at=L,
            ssh_target="owner@mac-only-view",
        )
        store(mac, own)
        digest = json.loads(json.dumps(registry.digest(mac)))
        remote.validate_digest(digest)
        dell = {"schema_version": 1, "machines": {}, "removed": {}}
        registry.merge(dell, digest, "DELL-XPS-8950", "b" * 32)
        received = dell["machines"][MAC]
        for field in registry.EXCHANGED:
            self.assertEqual(received.get(field), own.get(field), field)
        # The SSH target is each viewer's own fact. It never crosses an exchange.
        self.assertNotIn("ssh_target", digest["members"][0])
        self.assertEqual(received["ssh_target"], "")

    def test_a_renamed_machine_stays_one_member(self):
        dell = {"schema_version": 1, "machines": {}, "removed": {}}
        first = record("old-name", machine_id="c" * 32, recorded_at="2026-11-01T00:00:00+00:00")
        registry.merge(dell, {"members": [first], "removed": {}}, "dell", "b" * 32)
        dell["machines"]["old-name"]["ssh_target"] = "me@lab"
        renamed = dict(first, hostname="new-name", recorded_at="2026-11-02T00:00:00+00:00")
        changes = registry.merge(dell, {"members": [renamed], "removed": {}}, "dell", "b" * 32)
        self.assertEqual(changes["updated"], ["new-name (was old-name)"])
        self.assertEqual(sorted(dell["machines"]), ["new-name"])
        self.assertEqual(dell["machines"]["new-name"]["ssh_target"], "me@lab")
        self.assertEqual(dell["machines"]["new-name"]["aliases"], ["old-name"])
        self.assertEqual(registry.resolve(dell, "old-name"), "new-name")
        self.assertEqual(registry.resolve(dell, "me@lab"), "new-name")

    def test_before_its_profile_a_new_saved_name_renames_its_own_record(self):
        # A first enrollment that stopped before its profile may choose its name again.
        home = self.home("box")
        with patch("socket.gethostname", return_value="before"):
            registry.refresh(home, "0.10.1", home / ".fleet/backups/one", "SUCCESS")
        # The first run saved the name, so a host name change alone renames nothing.
        with patch("socket.gethostname", return_value="after"):
            registry.refresh(home, "0.10.1", home / ".fleet/backups/two", "SUCCESS")
        self.assertEqual(sorted(registry.load(home)["machines"]), ["before"])
        # Without a profile yet, a new saved name is a new choice, and it renames the record.
        registry.save_name(home, "after")
        latest = registry.refresh(home, "0.10.1", home / ".fleet/backups/three", "SUCCESS")
        machines = registry.load(home)["machines"]
        self.assertEqual(sorted(machines), ["after"])
        self.assertEqual(latest["aliases"], ["before"])
        self.assertEqual(latest["machine_id"], registry.machine_id(home))
        # Once enrolled, with a profile, Fleet never renames the machine.
        (home / ".fleet/profile.toml").write_text('domain = "core"\n')
        with self.assertRaisesRegex(ValueError, "already named after. Fleet never renames it"):
            registry.save_name(home, "third")

    def test_the_machine_name_always_needs_the_home_it_belongs_to(self):
        # Review F-070: a default home let a caller read the current user's saved name.
        for function in (run_index.machine_name, run_index.saved_name, registry.machine_name):
            with self.subTest(function=function.__qualname__), self.assertRaises(TypeError):
                function()

    def test_two_ids_with_one_name_both_list_with_the_collision_shown(self):
        dell = self.home("dell")
        store(dell, record("own", machine_id="b" * 32))
        twins = [
            record("ubuntu", machine_id="1" * 32, recorded_at="2026-11-01T00:00:00+00:00"),
            record("ubuntu", machine_id="2" * 32, recorded_at="2026-11-01T00:00:00+00:00"),
        ]
        registry.merge_into(dell, {"members": twins, "removed": {}})
        machines = registry.load(dell)["machines"]
        self.assertEqual(sorted(machines), ["own", "ubuntu", "ubuntu~22222222"])
        with patch("socket.gethostname", return_value="own"):
            blocks = registry.entities(dell, "0.10.1")
        notes = {block["name"].split(" · ")[0]: block["notes"] for block in blocks}
        self.assertIn(
            "Name collision: ubuntu~22222222 also reports ubuntu with another ID.", notes["ubuntu"]
        )
        self.assertEqual(len(notes["ubuntu~22222222"]), 1)

    def test_a_new_enrollment_with_an_existing_id_is_that_member(self):
        dell = {"schema_version": 1, "machines": {}, "removed": {}}
        member = record("lab", machine_id="c" * 32, recorded_at="2026-11-01T00:00:00+00:00")
        registry.merge(dell, {"members": [member], "removed": {}}, "dell", "b" * 32)
        dell["machines"]["lab"]["ssh_target"] = "lab"
        again = dict(member, hostname="lab.local", recorded_at="2026-11-03T00:00:00+00:00")
        registry.merge(dell, {"members": [again], "removed": {}}, "dell", "b" * 32)
        self.assertEqual(sorted(dell["machines"]), ["lab.local"])

    def test_an_older_fact_still_binds_its_id_to_a_record_from_before_ids(self):
        dell = {"schema_version": 1, "machines": {}, "removed": {}}
        saved = record("lab", recorded_at="2026-11-05T00:00:00+00:00", ssh_target="lab")
        dell["machines"]["lab"] = saved
        older = record("lab", machine_id="c" * 32, recorded_at="2026-11-01T00:00:00+00:00")
        changes = registry.merge(dell, {"members": [older], "removed": {}}, "dell", "b" * 32)
        self.assertEqual(changes["updated"], ["lab"])
        self.assertEqual(dell["machines"]["lab"]["machine_id"], "c" * 32)
        self.assertEqual(dell["machines"]["lab"]["recorded_at"], "2026-11-05T00:00:00+00:00")

    def test_the_owners_split_reconciles_on_the_mac_under_its_actual_name(self):
        """The Mac's own record and the deploy's record share one last result before IDs."""
        mac = self.home("mac")
        own = record(MAC, last_run=L, content_commit=C, result="SUCCESS", home="/Users/owner")
        alias = record(SSH_NAME, last_run=L, content_commit=C, result="SUCCESS")
        store(mac, own, alias)
        with patch("socket.gethostname", return_value=MAC):
            # A digest with a pre-ID record that carries this machine's own result is this machine.
            self.assertEqual(
                registry.merge(
                    registry.load(mac),
                    {"members": [dict(alias, recorded_at=L)], "removed": {}},
                    MAC,
                    "",
                )["dropped"],
                [SSH_NAME],
            )
            latest = registry.refresh(mac, "0.10.1", mac / ".fleet/backups/next", "SUCCESS")
        machines = registry.load(mac)["machines"]
        self.assertEqual(sorted(machines), [MAC])
        self.assertEqual(latest["aliases"], [SSH_NAME])

    def test_the_owners_split_reconciles_on_the_caller_at_the_next_contact(self):
        dell = self.home("dell")
        deployed = record(
            SSH_NAME, last_run=L, content_commit=C, result="SUCCESS", ssh_target=SSH_NAME
        )
        reported = record(MAC, domain_chosen=False, recorded_at=L)
        store(dell, record("DELL-XPS-8950", machine_id="b" * 32), deployed, reported)
        with patch("socket.gethostname", return_value="DELL-XPS-8950"):
            blocks = registry.entities(dell, "0.10.1")
            notes = [note for block in blocks for note in block["notes"]]
            self.assertIn(
                f"Possible duplicate of {MAC}: same last run. The next contact merges them "
                "once both machines run this Fleet version.",
                notes,
            )
            # The Mac answers at the deploy record's connection with its ID and its own name.
            now = dict(
                reported,
                machine_id="a" * 32,
                domain_chosen=True,
                content_commit=C,
                last_run="2026-11-20T09:00:00+00:00",
                recorded_at="2026-11-20T09:00:00+00:00",
                result="SUCCESS",
            )
            registry.merge_into(
                dell,
                {"members": [now], "removed": {}},
                contacted=SSH_NAME,
                answered={"machine_id": "a" * 32, "hostname": MAC},
            )
        machines = registry.load(dell)["machines"]
        self.assertEqual(sorted(machines), ["DELL-XPS-8950", MAC])
        survivor = machines[MAC]
        self.assertEqual(survivor["ssh_target"], SSH_NAME)
        self.assertEqual(survivor["machine_id"], "a" * 32)
        self.assertTrue(survivor["domain_chosen"])
        self.assertEqual(registry.resolve(registry.load(dell), SSH_NAME), MAC)

    def test_without_shared_evidence_both_records_stay(self):
        mac = self.home("mac")
        own = record(MAC, last_run=L, content_commit=C, home="/Users/owner")
        other = record(SSH_NAME, last_run="2026-11-18T00:00:00+00:00", content_commit=C)
        store(mac, own, other)
        with patch("socket.gethostname", return_value=MAC):
            registry.refresh(mac, "0.10.1", mac / ".fleet/backups/next", "SUCCESS")
            notes = [note for block in registry.entities(mac, "0.10.1") for note in block["notes"]]
        self.assertEqual(sorted(registry.load(mac)["machines"]), [MAC, SSH_NAME])
        self.assertFalse([note for note in notes if "Possible duplicate" in note])


class DeployIdentityTests(unittest.TestCase):
    setUp = test_deploy.DeployTests.setUp
    enroll = test_deploy.DeployTests.enroll
    cli = test_deploy.DeployTests.cli
    prepare = test_deploy.DeployTests.prepare
    call = test_deploy.DeployTests.call
    deploy = test_deploy.DeployTests.deploy
    registry = test_deploy.DeployTests.registry

    def test_a_deploy_lists_the_target_under_its_hostname_not_its_ssh_name(self):
        self.prepare()
        homes = self.base / "homes.json"
        environments = json.loads(homes.read_text())
        # The SSH config calls the target mini-pc. The machine itself reports target-host.local.
        environments["mini-pc"]["FLEET_MACHINE_NAME"] = "target-host.local"
        homes.write_text(json.dumps(environments))
        output = self.deploy("--return-target", "caller")
        self.assertIn(
            "mini-pc: enrolled as target-host.local, the name that machine reports.", output
        )
        caller = self.registry(self.home)
        self.assertEqual(sorted(caller), ["caller-box", "target-host.local"])
        member = caller["target-host.local"]
        self.assertEqual(
            (member["ssh_target"], member["domain"], member["domain_chosen"], member["enrolled"]),
            ("mini-pc", "core", True, True),
        )
        self.assertEqual(member["machine_id"], registry.machine_id(self.target))
        target = self.registry(self.target)
        self.assertEqual(sorted(target), ["caller-box", "target-host.local"])
        self.assertEqual(target["caller-box"]["ssh_target"], "caller")
        listing = self.call("machines", "list")
        self.assertIn("target-host.local", listing)
        self.assertNotIn("mini-pc ·", listing)
        # Commands accept the fleet name or the SSH target that reaches it.
        self.assertIn("target-host.local: status succeeded", self.call("status", "mini-pc"))
        self.assertIn(
            "target-host.local: status succeeded", self.call("status", "target-host.local")
        )
        self.assertEqual(sorted(self.registry(self.home)), ["caller-box", "target-host.local"])
        self.assertIn(
            "already a fleet member", self.call("machines", "install", "mini-pc", expected=1)
        )


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    argument_parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        usage()
