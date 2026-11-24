"""Drive the terminal screens with Textual's headless keyboard and mouse pilot.

Usage: python3 -m unittest discover -s tests/tui -v in the test environment.
"""

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from common import toml
import command
import registry
import remote
import tui_core
import ui_channel
import updates
import connections
import topology
import owned_views
from render_run import render
from tui import (
    ActionScreen,
    AreaScreen,
    CommandForm,
    ConnectionPicker,
    FleetApp,
    MachineActions,
    Review,
    styled,
)
from tui_navigation import ACTIONS, AREAS, RELATED
from textual.widgets import Input, OptionList, RichLog, Select, SelectionList, Static


class FixtureSession:
    """Use the actual dispatcher; mock only external core operations in tests."""

    async def run(
        self, action, output, event, confirm, backup=None, machine=None, selected=None, entry=None
    ):
        loop = asyncio.get_running_loop()

        async def deliver(callback, value):
            callback(value)

        def send(message, reply=False):
            if reply:
                answer = asyncio.run_coroutine_threadsafe(confirm(message), loop).result(10)
                return answer
            # Match CoreSession: finish reading its events before completing the action.
            asyncio.run_coroutine_threadsafe(deliver(event, message), loop).result(10)

        def execute():
            captured = io.StringIO()
            with (
                patch.object(ui_channel, "send", side_effect=send),
                contextlib.redirect_stdout(captured),
            ):
                result = tui_core.dispatch(action, backup, machine, selected, entry)
            for line in captured.getvalue().splitlines():
                asyncio.run_coroutine_threadsafe(deliver(output, line), loop).result(10)
            return result

        return await asyncio.to_thread(execute)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.profile = {
            "domain": "personal",
            "domain_chosen": True,
            "git_name": "Example Person",
            "git_email": "example@example.invalid",
            "pins": {},
            "groups": {"helpers": True},
            "content_repo": "https://github.com/example/dotfiles",
        }
        profile = self.home / ".fleet/profile.toml"
        profile.parent.mkdir(parents=True)
        profile.write_text(toml(self.profile))
        backup = self.home / ".fleet/backups/20261116-140000"
        backup.mkdir(parents=True)
        (backup / "MANIFEST.json").write_text("[]")
        (backup / "RUN.json").write_text('{"result":"SUCCESS"}')
        (self.home / "VERSION").write_text("0.2.0\n")
        self.record = json.loads((ROOT / "tests/fixtures/run-results/changed.json").read_text())
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for context in (
            patch.dict(os.environ, {"HOME": str(self.home), "TERM": "xterm-256color"}),
            patch.object(tui_core, "ROOT", self.home),
            patch.object(tui_core.socket, "gethostname", return_value="example-hub"),
            patch.object(tui_core, "commit", return_value="abcdef0123456789" * 2 + "abcdef01"),
            patch.object(command, "status", return_value=""),
            patch.object(command, "pin_rows", return_value=[]),
            patch.object(
                command.enrollment, "installed", return_value={"node": [{"version": "24.14.1"}]}
            ),
            patch.object(
                command,
                "checkout_state",
                return_value="abcdef01 · 0 behind last fetched upstream · clean",
            ),
            patch("command_display.shutil.which", return_value=None),
        ):
            self.stack.enter_context(context)

    async def settle(self, pilot, app):
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def open_command(self, pilot, app, action):
        while len(app.screen_stack) > 1:
            await pilot.press("escape")
            await self.settle(pilot, app)
        area = next(name for name, actions in AREAS.items() if action in actions)
        menu = app.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index(area)
        menu.focus()
        await pilot.press("enter")
        options = app.screen.query_one("#area-actions", OptionList)
        options.highlighted = options.get_option_index(action)
        options.focus()
        await pilot.press("enter")
        await pilot.pause()

    async def test_dashboard_from_store_at_80_columns(self):
        app = FleetApp(FixtureSession())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.settle(pilot, app)
            facts = app.query_one("#facts", Static).render().plain
            for text in (
                "example-hub",
                "personal · chosen",
                self.profile["content_repo"],
                "abcdef01",
                "0.2.0",
                "not checked",
                "2026-11-16 14:00:00",
                "Drift        none",
            ):
                self.assertIn(text, facts)
            self.assertEqual(app.screen.max_scroll_y, 0)
            self.assertLessEqual(app.query_one(".keys").region.bottom, 24)
            # Capture actual compositor cells, including selection and padding.
            lines = [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]
            text = "\n".join(lines).rstrip() + "\n"
            golden = ROOT / "tests/fixtures/tui/dashboard-80x24.txt"
            self.assertEqual(text, golden.read_text())

    async def test_logs_lists_attempts_and_opens_selected_detail(self):
        import run_index

        identifier = run_index.begin(self.home, "configure")
        run_index.update(
            self.home, identifier, result="FAILED", stage="profile", error="Git email is empty."
        )
        app = FleetApp(FixtureSession())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.settle(pilot, app)
            await app.open_command("logs")
            await self.settle(pilot, app)
            options = app.screen.query_one("#actions", OptionList)
            self.assertIn("configure", options.get_option("entry-0").prompt)
            options.highlighted = 0
            options.focus()
            await pilot.press("enter")
            await self.settle(pilot, app)
            text = "\n".join(line.text for line in app.screen.query_one("#output", RichLog).lines)
            self.assertIn("Stage reached", text)
            self.assertIn("Git email is empty.", text)
            self.assertEqual(len(run_index.entries(self.home)), 1)

    async def test_updates_call_check_and_update_cores_with_review(self):
        def check(home, profile, display=True, marketplaces=False, tools=False):
            print("Manager: 3 commits behind @ abcdef01")
            print("Content: up to date @ abcdef01")
            return 1

        def update(home):
            if ui_channel.review(self.record):
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(updates, "check", side_effect=check) as checked,
            patch.object(command, "update", side_effect=update) as updated,
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "check")
                await self.settle(pilot, app)
                self.assertEqual(checked.call_count, 1)
                self.assertIsInstance(app.screen, ActionScreen)
                self.assertFalse(
                    app.screen.query_one("#actions", OptionList).get_option("update").disabled
                )
                await pilot.press("down", "enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                await pilot.press(*"confirm", "enter")
                await self.settle(pilot, app)
                self.assertEqual(updated.call_count, 1)
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("6 · Next steps", text)
                self.assertIn("fleet update", text)

    async def test_configuration_and_system_views_use_the_cli_actions(self):
        app = FleetApp(FixtureSession())
        with (
            patch.object(owned_views, "run", return_value=""),
            patch.object(command, "main", wraps=command.main) as called,
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                for action in (
                    "domain",
                    "groups",
                    "pins",
                    "secrets",
                    "content",
                    "version",
                    "addons",
                    "help",
                    "tui",
                ):
                    await self.open_command(pilot, app, action)
                    await self.settle(pilot, app)
                    called.assert_called_with(list(ACTIONS[action][1]))
                    self.assertTrue(app.screen.query_one(RichLog).lines)

    async def test_domain_form_calls_the_apply_core_after_review(self):
        applied = []

        def apply(arguments, **kwargs):
            if ui_channel.review(self.record):
                applied.append(arguments)
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with patch.object(command.manager, "main", side_effect=apply):
            async with app.run_test(size=(32, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "domain")
                await self.settle(pilot, app)
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, CommandForm)
                app.screen.query_one(Select).value = "work"
                app.screen.query_one("#submit-form").focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                self.assertEqual(applied, [])
                await pilot.press(*"confirm", "enter")
                await self.settle(pilot, app)
                self.assertEqual(applied, [["--defaults", "--domain", "work"]])
                self.assertIn(
                    "6 · Next steps",
                    "\n".join(line.text for line in app.screen.query_one(RichLog).lines),
                )

    async def test_uninstall_requires_its_word_and_cancel_keeps_the_profile(self):
        removed = []

        def transaction(home, operations, label, selected, word, trees, **options):
            if command.confirm(word, label, self.record):
                removed.append(word)
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(ui_channel, "active", return_value=True),
            patch.object(command, "managed_paths", return_value={command.PROFILE}),
            patch.object(command, "transaction", side_effect=transaction),
        ):
            async with app.run_test(size=(32, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "uninstall")
                self.assertIsInstance(app.screen, CommandForm)
                app.screen.query_one("#submit-form").focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                self.assertEqual(app.screen.request["word"], "uninstall")
                await pilot.press("u", "enter")
                self.assertEqual(removed, [])
                await pilot.press("escape")
                await self.settle(pilot, app)
                self.assertEqual(removed, [])
                self.assertTrue((self.home / command.PROFILE).is_file())
                await self.open_command(pilot, app, "uninstall")
                app.screen.query_one("#submit-form").focus()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press(*"uninstall", "enter")
                await self.settle(pilot, app)
                self.assertEqual(removed, ["uninstall"])

    async def test_remote_missing_command_is_classified_on_screen(self):
        from types import SimpleNamespace

        record = registry.machine(self.home, "0.2.0")
        record.update(hostname="mini-pc", ssh_target="mini-pc")
        path = self.home / registry.REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(registry.serialize({"schema_version": 1, "machines": {"mini-pc": record}}))
        app = FleetApp(FixtureSession())
        with patch.object(
            connections,
            "invoke",
            return_value=SimpleNamespace(
                returncode=1, stdout="", stderr="zsh: private shell detail"
            ),
        ):
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "machines")
                await self.settle(pilot, app)
                await pilot.press("enter")
                await self.settle(pilot, app)
                output = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("Fleet is not installed", output)
                self.assertNotIn("private shell detail", output)
                self.assertNotIn("zsh:", output)

    async def test_machines_offer_remote_actions_only_for_connections(self):
        data = {"schema_version": 1, "machines": {}}
        for name, target in (("beelink", "mini-pc"), ("offline-record", "")):
            record = registry.machine(self.home, "0.2.0")
            record.update(hostname=name, ssh_target=target)
            data["machines"][name] = record
        path = self.home / registry.REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(registry.serialize(data))

        def execute(home, machine, action, **options):
            print("Machine · " + machine)
            if action == "update":
                print(render(self.record), end="")
            else:
                print(action + " finished on target")
            return 0

        app = FleetApp(FixtureSession())
        with patch.object(remote, "execute", side_effect=execute) as called:
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "machines")
                await self.settle(pilot, app)
                options = app.screen.query_one("#actions", OptionList)
                self.assertEqual(
                    options.option_count, len(remote.CAPABLE) + len(RELATED["machines"]) + 1
                )
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("offline-record", text)
                self.assertIn("no connection", text)
                for action in remote.CAPABLE:
                    options.highlighted = options.get_option_index("remote-0-" + action)
                    options.focus()
                    await pilot.press("enter")
                    await self.settle(pilot, app)
                    called.assert_called_with(self.home, "beelink", action, check=False, stat=False)
                    self.assertEqual(app.screen.machine, "beelink")
                    text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                    self.assertIn("Machine · beelink", text)
                    if action == "update":
                        self.assertIn("6 · Next steps", text)
                    await pilot.press("escape")
                    await self.settle(pilot, app)
                self.assertEqual(called.call_count, 5)

    async def test_rollback_requires_exact_typed_word_and_escape_cancels(self):
        restored = []

        def restore(args, home):
            if command.confirm("restore", "Restore these files.", self.record):
                restored.append(args.backup)
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(ui_channel, "active", return_value=True),
            patch.object(command, "restore_command", side_effect=restore),
        ):
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "backups")
                await self.settle(pilot, app)
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                await pilot.press("enter", "r", "enter")
                self.assertEqual(restored, [])
                self.assertIsInstance(app.screen, Review)
                await pilot.press("escape")
                await self.settle(pilot, app)
                self.assertEqual(restored, [])
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press(*"restore", "enter")
                await self.settle(pilot, app)
                self.assertEqual(len(restored), 1)

    async def test_backup_checkboxes_delete_only_two_selected_after_typed_confirm(self):
        from common import atomic

        folders = [self.home / ".fleet/backups/20261116-140000"]
        for day in (17, 18):
            folder = self.home / ".fleet/backups" / f"202611{day}-140000"
            atomic(folder / "MANIFEST.json", "[]")
            atomic(folder / "RUN.json", '{"result":"SUCCESS"}')
            atomic(folder / "home/.file", "original")
            folders.append(folder)
        app = FleetApp(FixtureSession())
        with patch.object(ui_channel, "active", return_value=True):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "backups")
                await self.settle(pilot, app)
                selections = app.screen.query_one("#backup-selection", SelectionList)
                selections.focus()
                await pilot.press("space", "down", "space")
                selected = {Path(path) for path in selections.selected}
                self.assertEqual(len(selected), 2)
                actions = app.screen.query_one("#actions", OptionList)
                actions.highlighted = actions.get_option_index("backup-delete-selected")
                actions.focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("2 backups", text)
                self.assertIn("bytes", text)
                self.assertTrue(all(folder.exists() for folder in folders))
                await pilot.press("d", "enter")
                self.assertIsInstance(app.screen, Review)
                self.assertTrue(all(folder.exists() for folder in folders))
                await pilot.press("escape")
                await self.settle(pilot, app)
                self.assertTrue(all(folder.exists() for folder in folders))
                actions = app.screen.query_one("#actions", OptionList)
                actions.highlighted = actions.get_option_index("backup-delete-selected")
                actions.focus()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press(*"delete", "enter")
                await self.settle(pilot, app)
                self.assertTrue(all(not folder.exists() for folder in selected))
                self.assertTrue(all(folder.exists() for folder in set(folders) - selected))
                self.assertEqual(
                    app.screen.query_one("#backup-selection", SelectionList).option_count, 1
                )
                self.assertTrue(list(self.home.glob(".fleet/cleanup-runs/*/RUN.json")))

    async def test_phone_width_keyboard_and_plain_themes(self):
        for environment in ({"NO_COLOR": ""}, {"TERM": "dumb"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                app = FleetApp(FixtureSession())
                async with app.run_test(size=(32, 24)) as pilot:
                    await self.settle(pilot, app)
                    self.assertEqual(app.theme, "fleet-plain")
                    self.assertEqual(styled("  + ~/.new", app.plain).spans, [])
                    for widget in app.screen.query("Static, OptionList"):
                        self.assertLessEqual(widget.region.right, 32)
                    await self.open_command(pilot, app, "status")
                    await self.settle(pilot, app)
                    self.assertIsInstance(app.screen, ActionScreen)
                    await pilot.press("tab", "escape", "escape")
                    await self.settle(pilot, app)
                    self.assertEqual(len(app.screen_stack), 1)
                    await pilot.press("q")

    async def test_configure_closes_the_interface(self):
        app = FleetApp(FixtureSession())
        async with app.run_test() as pilot:
            await self.settle(pilot, app)
            await self.open_command(pilot, app, "configure")
        self.assertEqual(app.return_value, "configure")

    async def test_topology_uses_shared_tree_and_only_connected_actions(self):
        records = {}
        for name, target in (("mini-pc", "mini-pc"), ("offline-record", "")):
            record = registry.machine(self.home, "0.2.0")
            record.update(hostname=name, ssh_target=target)
            records[name] = record
        path = self.home / registry.REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(registry.serialize({"schema_version": 1, "machines": records}))
        app = FleetApp(FixtureSession())
        with patch.object(remote, "execute", return_value=0) as execute:
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "topology")
                await self.settle(pilot, app)
                self.assertEqual(app.screen.action, "topology")
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("Topology from example-hub", text)
                self.assertIn("offline-record", text)
                self.assertIn("never contacted", text)
                options = app.screen.query_one("#actions", OptionList)
                self.assertEqual(options.option_count, 2, text)
                self.assertEqual(str(options.get_option("machine-0").prompt), "mini-pc")
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, MachineActions)
                self.assertEqual(app.screen.query_one(OptionList).option_count, 6)
                await pilot.press("enter")
                await self.settle(pilot, app)
                execute.assert_called_with(self.home, "mini-pc", "status", check=False, stat=False)

    async def test_machines_add_picker_registers_and_tests_the_selected_host(self):
        from types import SimpleNamespace

        config = self.home / ".ssh/config"
        config.parent.mkdir()
        config.write_text("Host mini-pc other *.example\n")

        def save(home, edit):
            data = registry.load(home)
            edit(data)
            path = home / registry.REGISTRY
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(registry.serialize(data))
            print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(ui_channel, "active", return_value=True),
            patch.object(command, "change_registry", side_effect=save),
            patch.object(
                connections, "invoke", return_value=SimpleNamespace(returncode=0)
            ) as probe,
        ):
            async with app.run_test(size=(32, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "add-connection")
                self.assertIsInstance(app.screen, ConnectionPicker)
                self.assertEqual(app.screen.query_one(OptionList).option_count, 2)
                self.assertEqual(app.screen.query_one(Input).region.right <= 32, True)
                await pilot.press("enter")
                await self.settle(pilot, app)
                probe.assert_called_once_with("mini-pc", "true", probe=True)
                self.assertEqual(
                    registry.load(self.home)["machines"]["mini-pc"]["ssh_target"], "mini-pc"
                )
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("connection test passed", text)
                self.assertIn("6 · Next steps", text)

    async def test_machines_add_without_config_types_target_and_cancel_saves_nothing(self):
        from types import SimpleNamespace

        saved = []
        app = FleetApp(FixtureSession())
        with (
            patch.object(ui_channel, "active", return_value=True),
            patch.object(
                command, "set_machine_ssh", side_effect=lambda *args: saved.append(args) or 0
            ),
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "add-connection")
                self.assertIsInstance(app.screen, ConnectionPicker)
                self.assertIn("No SSH config", app.screen.request["notice"])
                await pilot.press("escape")
                await self.settle(pilot, app)
                self.assertEqual(saved, [])
                self.assertFalse((self.home / registry.REGISTRY).exists())
                await self.open_command(pilot, app, "add-connection")
                app.screen.query_one(Input).value = "user@host"
                await pilot.press("enter")
                await self.settle(pilot, app)
                self.assertEqual(saved, [(self.home, "user@host", "user@host")])

    async def test_diff_and_read_only_screens_keep_core_output(self):
        app = FleetApp(FixtureSession())
        with patch.object(
            command,
            "preview",
            side_effect=lambda *args: (
                print("No differences. Your files match fixture @ abcdef01.") or 0
            ),
        ) as preview:
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                # Mouse and keyboard activate the same menu options.
                await self.open_command(pilot, app, "diff")
                await self.settle(pilot, app)
                self.assertEqual(preview.call_count, 1)
                lines = [line.text for line in app.screen.query_one(RichLog).lines]
                self.assertEqual(lines, ["No differences. Your files match fixture @ abcdef01."])
                await pilot.press("escape")
                await self.settle(pilot, app)
                await pilot.click("#area-actions", offset=(3, 1))
                await self.settle(pilot, app)
                self.assertIsInstance(app.screen, ActionScreen)
                self.assertEqual(app.screen.action, "status")

    async def test_failed_check_never_offers_update(self):
        app = FleetApp(FixtureSession())
        with (
            patch.object(updates, "check", return_value=2),
            patch.object(command, "update") as update,
        ):
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                await self.open_command(pilot, app, "check")
                await self.settle(pilot, app)
                self.assertTrue(
                    app.screen.query_one("#actions", OptionList).get_option("update").disabled
                )
                self.assertFalse(update.called)

    async def test_review_wraps_at_phone_width_without_hidden_columns(self):
        app = FleetApp(FixtureSession())
        async with app.run_test(size=(32, 24)) as pilot:
            await self.settle(pilot, app)
            await app.push_screen(
                Review(
                    {
                        "record": self.record,
                        "word": "restore",
                        "explanation": "Restore these files.",
                    }
                )
            )
            await pilot.pause()
            output = app.screen.query_one("#review-text", RichLog)
            self.assertEqual(output.max_scroll_x, 0)
            self.assertLessEqual(output.region.right, 32)
            await pilot.press("escape")
            self.assertEqual(len(app.screen_stack), 1)

    async def test_escape_and_quit_cancel_a_running_read(self):
        cancelled = asyncio.Event()

        class SlowCheck(FixtureSession):
            async def run(self, action, *args, **kwargs):
                if action == "check":
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                else:
                    return await super().run(action, *args, **kwargs)

        app = FleetApp(SlowCheck())
        async with app.run_test() as pilot:
            await self.settle(pilot, app)
            await self.open_command(pilot, app, "check")
            await pilot.pause()
            self.assertTrue(app.busy)
            await pilot.press("escape")
            await self.settle(pilot, app)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(len(app.screen_stack), 2)
            cancelled.clear()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("q")
            self.assertTrue(cancelled.is_set())

    def stopping_app(self, order, release=None):
        """Return an app whose first dashboard stays pending, like a slow core process.

        Its cleanup takes a moment, as a real child process does. ORDER records when the
        dashboard stopped and when the app exited, so each test can check that order.
        """

        class SlowDashboard(FixtureSession):
            calls = 0

            async def run(self, action, output, event, *args, **kwargs):
                if action == "dashboard" and not SlowDashboard.calls:
                    SlowDashboard.calls += 1
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await asyncio.sleep(0.05)
                        order.append("dashboard stopped")
                if action == "uninstall":
                    event({"kind": "uninstalled", "value": True})
                    return 0
                if action == "update" and release:
                    await release.wait()
                    return 0
                return await super().run(action, output, event, *args, **kwargs)

        class RecordingApp(FleetApp):
            # Textual reads CSS_PATH next to the defining module, so name the real file.
            CSS_PATH = Path(sys.modules[FleetApp.__module__].__file__).with_name(FleetApp.CSS_PATH)

            def exit(self, *args, **kwargs):
                order.append("exit")
                super().exit(*args, **kwargs)

        return RecordingApp(SlowDashboard())

    async def choose(self, pilot, app, action):
        """Open ACTION from the main menu without waiting for the pending dashboard."""
        area = next(name for name, actions in AREAS.items() if action in actions)
        menu = app.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index(area)
        menu.focus()
        await pilot.press("enter")
        await pilot.pause()
        options = app.screen.query_one("#area-actions", OptionList)
        options.highlighted = options.get_option_index(action)
        options.focus()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.02)

    async def test_leaving_for_a_terminal_command_stops_the_dashboard_first(self):
        order = []
        app = self.stopping_app(order)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self.choose(pilot, app, "configure")
        self.assertEqual(app.return_value, "configure")
        self.assertEqual(order[:2], ["dashboard stopped", "exit"])

    async def test_back_after_uninstall_stops_the_dashboard_first(self):
        order = []
        app = self.stopping_app(order)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self.choose(pilot, app, "uninstall")
            self.assertTrue(app.uninstalled)
            await pilot.press("escape")
            for _ in range(20):
                await pilot.pause(0.02)
        self.assertEqual(order[:2], ["dashboard stopped", "exit"])

    async def test_back_to_the_menu_while_the_dashboard_loads_stops_it_first(self):
        # Review F-046: Esc starts a new dashboard. The loading one must not be left behind.
        order = []
        app = self.stopping_app(order)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one("#menu", OptionList)
            menu.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, AreaScreen)
            await pilot.press("escape")
            for _ in range(20):
                await pilot.pause(0.02)
            await pilot.press("q")
            for _ in range(20):
                await pilot.pause(0.02)
        self.assertEqual(order[:2], ["dashboard stopped", "exit"])

    async def test_a_quit_deferred_until_a_change_ends_stops_the_dashboard_first(self):
        order = []
        release = asyncio.Event()
        app = self.stopping_app(order, release)
        async with app.run_test() as pilot:
            await pilot.pause()
            await self.choose(pilot, app, "update")
            self.assertTrue(app.busy and app.changing)
            await pilot.press("q")
            self.assertTrue(app.exit_requested)
            release.set()
            for _ in range(20):
                await pilot.pause(0.02)
        self.assertEqual(order[:2], ["dashboard stopped", "exit"])


if __name__ == "__main__":
    unittest.main()
