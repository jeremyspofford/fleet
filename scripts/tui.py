"""Show Fleet's existing commands in a keyboard-first terminal interface.

Usage: dotfleet tui. Arrows and Tab move; Enter selects; Esc goes back; q quits.
Configure closes this interface and runs the existing enrollment wizard.
"""

import argparse
import os
from pathlib import Path
import sys

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import BUILTIN_THEMES, Theme
from textual.worker import WorkerCancelled, get_current_worker
from textual.widgets import Button, Input, OptionList, RichLog, Select, SelectionList, Static
from textual.widgets.option_list import Option

from render_run import human, render
import remote
import style
from tui_session import CoreSession
from tui_navigation import ACTIONS, AREAS, CHANGING, RELATED

ROOT = Path(__file__).resolve().parents[1]
MENU = tuple(AREAS)


def plain_theme():
    return "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"


def palette(name):
    code = int(style.COLORS[name][2:-1]) - 30
    return "ansi_" + ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")[code]


def styled(line, plain=False, diff=False):
    if plain:
        return Text(line)
    color = "CYAN" if line == style.RULE or " · " in line[:8] else style.line_palette(line, diff)
    return Text.from_ansi(style.COLORS[color] + line + style.CLEAR)


class Review(ModalScreen[str]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static("Review before apply", classes="heading")
            yield RichLog(id="review-text", wrap=True, highlight=False, min_width=1)
            yield Static(
                self.request["explanation"]
                + f" Type {self.request['word']} to proceed, or cancel. Press Enter.",
                id="confirm-help",
            )
            yield Static("Tab moves focus; arrows scroll the review. Esc cancels.", classes="keys")
            yield Input(placeholder=f"Type {self.request['word']} or cancel", id="confirmation")
            yield Static("", id="confirm-error")
            yield Button("Cancel", id="cancel")

    def on_mount(self):
        output = self.query_one("#review-text", RichLog)
        for line in render(self.request["record"], future=True).splitlines():
            output.write(styled(line, self.app.plain))
        output.scroll_home(animate=False)
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, event):
        answer = event.value.strip()
        if answer in (self.request["word"], "cancel"):
            self.dismiss(answer)
        else:
            self.query_one("#confirm-error", Static).update(
                "Not confirmed. Type the exact word shown above."
            )

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss("cancel")


class ConnectionPicker(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static(self.request["title"], classes="heading")
            yield Static(self.request["notice"] or "Choose an unregistered SSH host.")
            yield OptionList(
                *(
                    Option(f"{number + 1}. {host}", id=str(number))
                    for number, host in enumerate(self.request["items"])
                ),
                id="host-options",
            )
            yield Static(
                "Enter selects a host. Tab moves to a typed target. Esc cancels.", classes="keys"
            )
            yield Static("Or enter user@host or user@IP, then press Enter.")
            yield Input(placeholder="SSH target", id="ssh-target")
            yield Static("", id="target-error")
            yield Button("Cancel", id="cancel")

    def on_mount(self):
        if self.request["items"]:
            self.query_one(OptionList).focus()
        else:
            self.query_one(Input).focus()

    def choose(self, target):
        try:
            remote.validate_target(target)
        except ValueError as error:
            self.query_one("#target-error", Static).update(str(error))
            return
        self.dismiss(target)

    @on(OptionList.OptionSelected, "#host-options")
    def selected(self, event):
        self.choose(self.request["items"][int(event.option.id)])

    @on(Input.Submitted, "#ssh-target")
    def typed(self, event):
        self.choose(event.value.strip())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss(None)


class CommandForm(ModalScreen[dict | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Static(self.request["title"], classes="heading")
            yield Static(self.request["detail"], classes="form-detail")
            with VerticalScroll(id="form-fields"):
                for field in self.request["fields"]:
                    yield Static(field["label"], classes="form-label")
                    if field["choices"]:
                        yield Select(
                            field["choices"],
                            value=field["default"],
                            allow_blank=False,
                            id="field-" + field["name"],
                        )
                    else:
                        yield Input(value=field["default"], id="field-" + field["name"])
            yield Static("Tab moves. Enter selects or submits. Esc cancels.", classes="keys")
            yield Static("", id="form-error")
            yield Button("Continue", id="submit-form")
            yield Button("Cancel", id="cancel-form")

    def on_mount(self):
        self.query_one("#field-" + self.request["fields"][0]["name"]).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#submit-form")
    def submit(self):
        answer = {}
        for field in self.request["fields"]:
            value = self.query_one("#field-" + field["name"]).value
            if not isinstance(value, str) or field["required"] and not value.strip():
                self.query_one("#form-error", Static).update(field["label"] + " needs a value.")
                return
            answer[field["name"]] = value.strip()
        self.dismiss(answer)

    @on(Button.Pressed, "#cancel-form")
    def action_cancel(self):
        self.dismiss(None)


class AreaScreen(Screen):
    def __init__(self, area):
        super().__init__()
        self.area = area

    def compose(self) -> ComposeResult:
        yield Static("Fleet · " + self.area, classes="heading")
        yield OptionList(
            *(Option(ACTIONS[action][0], id=action) for action in AREAS[self.area]),
            Option("Back", id="back"),
            id="area-actions",
        )
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    @on(OptionList.OptionSelected, "#area-actions")
    async def selected(self, event):
        if event.option.id == "back":
            await self.app.action_back()
        else:
            self.app.open_command(event.option.id)


class MachineActions(Screen):
    def __init__(self, machine):
        super().__init__()
        self.machine = machine

    def compose(self) -> ComposeResult:
        yield Static("Fleet · " + self.machine, classes="heading")
        yield OptionList(
            *(Option(action.capitalize(), id=action) for action in remote.CAPABLE),
            Option("Back", id="back"),
            id="machine-actions",
        )
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    @on(OptionList.OptionSelected, "#machine-actions")
    async def selected(self, event):
        if event.option.id == "back":
            await self.app.action_back()
        else:
            self.app.push_screen(ActionScreen(event.option.id, self.machine))


class ActionScreen(Screen):
    def __init__(self, action, machine=None):
        super().__init__()
        self.action = action
        self.machine = machine
        self.backups = []
        self.entries = []
        self.connections = []
        self.uninstalled = False

    def compose(self) -> ComposeResult:
        title = ACTIONS[self.action][0]
        if self.machine:
            title += " · " + self.machine
        yield Static("Fleet · " + title, classes="heading")
        yield RichLog(id="output", wrap=True, highlight=False, min_width=1)
        if self.action == "backups":
            yield SelectionList(id="backup-selection")
            yield Static(
                "Space toggles a backup. Tab moves to restore or delete actions.", classes="keys"
            )
        yield Static("Working…", id="progress")
        yield OptionList(Option("Back", id="back"), id="actions")
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    def on_mount(self):
        self.app.perform_action(self, self.action)

    def options(self, result, action):
        options = self.query_one("#actions", OptionList)
        options.clear_options()
        if self.uninstalled:
            options.add_option(Option("Close Fleet", id="close"))
            options.disabled = False
            options.focus()
            return
        if self.action == "check":
            options.add_option(Option("Check again", id="check"))
            options.add_option(
                Option(
                    "Update manager and content",
                    id="update",
                    disabled=action != "check" or result != 1,
                )
            )
        elif self.action == "backups":
            for index, backup in enumerate(self.backups):
                options.add_option(Option("Restore " + Path(backup).name, id=f"restore-{index}"))
        elif self.action == "logs":
            for index, entry in enumerate(self.entries):
                options.add_option(Option(entry["label"], id=f"entry-{index}"))
        elif self.action == "machines":
            for index, machine in enumerate(self.connections):
                for action in remote.CAPABLE:
                    options.add_option(
                        Option(action.capitalize() + " · " + machine, id=f"remote-{index}-{action}")
                    )
        elif self.action == "topology":
            for index, machine in enumerate(self.connections):
                options.add_option(Option(machine, id=f"machine-{index}"))
        for action in RELATED.get(self.action, ()):
            disabled = (
                action == "backup-delete-selected"
                and not self.query_one("#backup-selection", SelectionList).selected
            )
            options.add_option(Option(ACTIONS[action][0], id=action, disabled=bool(disabled)))
        options.add_option(Option("Back", id="back"))
        options.disabled = False
        options.highlighted = 0
        options.focus()

    @on(SelectionList.SelectedChanged, "#backup-selection")
    def backup_selection_changed(self):
        options = self.query_one("#actions", OptionList)
        if any(option.id == "backup-delete-selected" for option in options.options):
            options.get_option("backup-delete-selected").disabled = not self.query_one(
                SelectionList
            ).selected
            options.refresh()

    @on(OptionList.OptionSelected, "#actions")
    async def selected(self, event):
        choice = event.option.id
        if choice == "close":
            self.app.exit()
        elif choice == "back":
            await self.app.action_back()
        elif choice == "backup-delete-selected":
            selected = self.query_one("#backup-selection", SelectionList).selected
            self.app.perform_action(self, choice, selected=[Path(path).name for path in selected])
        elif choice == "backup-delete-all":
            self.app.perform_action(self, choice)
        elif choice.startswith("restore-"):
            self.app.perform_action(
                self, "rollback", self.backups[int(choice.removeprefix("restore-"))]
            )
        elif choice.startswith("entry-"):
            entry = self.entries[int(choice.removeprefix("entry-"))]
            self.app.perform_action(self, "log-show", entry=entry["id"])
        elif choice.startswith("remote-"):
            _, index, action = choice.split("-", 2)
            self.app.push_screen(ActionScreen(action, self.connections[int(index)]))
        elif choice.startswith("machine-"):
            self.app.push_screen(
                MachineActions(self.connections[int(choice.removeprefix("machine-"))])
            )
        elif choice in ("check", "update") and self.action == "check":
            self.app.perform_action(self, choice)
        else:
            self.app.open_command(choice)


class FleetApp(App[str]):
    CSS_PATH = "tui.tcss"
    TITLE = "Fleet"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("escape", "back", "Back"), ("q", "quit", "Quit")]

    def __init__(self, session=None):
        super().__init__()
        self.session = session or CoreSession(ROOT)
        self.plain = plain_theme()
        self.rows = []
        self.busy = False
        self.changing = False
        self.exit_requested = False
        self.uninstalled = False
        self.worker = None
        self.operation_worker = None
        colors = {key: "ansi_white" if self.plain else palette(key) for key in style.COLORS}
        self.register_theme(
            Theme(
                name="fleet-plain" if self.plain else "fleet",
                primary=colors["CYAN"],
                secondary=colors["BLUE"],
                accent=colors["MAGENTA"],
                warning=colors["YELLOW"],
                error=colors["RED"],
                success=colors["GREEN"],
                background="ansi_black",
                surface="ansi_black",
                panel="ansi_black",
                foreground="ansi_white",
                ansi=True,
                variables={
                    key: value.replace("ansi_bright_blue", "ansi_white") if self.plain else value
                    for key, value in BUILTIN_THEMES["ansi-dark"].variables.items()
                },
            )
        )
        self.theme = "fleet-plain" if self.plain else "fleet"

    def compose(self) -> ComposeResult:
        yield Static("Fleet · this machine", classes="heading")
        yield Static("Loading local state…", id="facts")
        yield OptionList(*(Option(area, id=area) for area in MENU), id="menu")
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    def on_mount(self):
        self.worker = self.load_dashboard()
        self.query_one("#menu", OptionList).focus()

    def render_facts(self):
        width = max(12, self.size.width - 2)
        lines = []
        for key, value in self.rows:
            text = key.ljust(11) + "  " + human(value, str(Path.home()))
            line = Text(text, no_wrap=True)
            line.truncate(width, overflow="ellipsis")
            lines.append(line.plain)
        self.screen_stack[0].query_one("#facts", Static).update(Text("\n".join(lines)))

    def on_resize(self):
        if self.is_mounted and self.rows:
            self.render_facts()

    @work
    async def load_dashboard(self):
        def event(message):
            if message["kind"] == "dashboard":
                self.rows = message["rows"]
                self.render_facts()

        errors = []
        try:
            result = await self.session.run("dashboard", errors.append, event, self.confirm)
            if result:
                self.screen_stack[0].query_one("#facts", Static).update(Text("\n".join(errors)))
        except Exception as error:
            self.screen_stack[0].query_one("#facts", Static).update(Text("Error: " + str(error)))

    async def confirm(self, request):
        if request.get("kind") == "choose":
            return await self.push_screen_wait(ConnectionPicker(request))
        if request.get("kind") == "form":
            return await self.push_screen_wait(CommandForm(request))
        return await self.push_screen_wait(Review(request))

    @on(OptionList.OptionSelected, "#menu")
    def open_action(self, event):
        self.push_screen(AreaScreen(event.option.id))

    def open_command(self, action):
        if action in ("configure", "machine-install"):
            # Both need the real terminal: the wizard reads it directly, here or over SSH.
            self.exit(action)
        else:
            self.push_screen(ActionScreen(action))

    @work
    async def perform_action(self, screen, action, backup=None, selected=None, entry=None):
        if self.busy:
            return
        self.busy = True
        self.operation_worker = get_current_worker()
        self.changing = action in CHANGING
        output = screen.query_one("#output", RichLog)
        output.clear()
        screen.query_one("#actions", OptionList).disabled = True
        progress = screen.query_one("#progress", Static)
        progress.update(
            "Choose a target; saving is followed by one SSH test."
            if action == "add-connection"
            else "Working on " + screen.machine + ". The target runs without prompts."
            if screen.machine
            else "Working… Changes require review."
            if self.changing
            else "Working…"
        )

        def event(message):
            if message["kind"] == "backups":
                screen.backups = message["items"]
                if screen.action == "backups":
                    choices = screen.query_one("#backup-selection", SelectionList)
                    selected = set(choices.selected)
                    choices.clear_options()
                    choices.add_options(
                        [(Path(path).name, path, path in selected) for path in screen.backups]
                    )
                    choices.highlighted = 0 if screen.backups else None
            elif message["kind"] == "logs":
                screen.entries = message["items"]
            elif message["kind"] == "connections":
                screen.connections = message["items"]
            elif message["kind"] == "uninstalled":
                self.uninstalled = screen.uninstalled = message["value"]

        try:
            result = await self.session.run(
                action,
                lambda line: output.write(styled(line, self.plain, action == "diff")),
                event,
                self.confirm,
                backup,
                **({"machine": screen.machine} if screen.machine else {}),
                **({"selected": selected} if selected is not None else {}),
                **({"entry": entry} if entry is not None else {}),
            )
            normal = result == 0 or action in ("check", "diff") and result == 1
            progress.update(
                "Finished."
                if normal
                else "Stopped on " + screen.machine + ". Resolve the target error before retrying."
                if screen.machine
                else "Stopped. Read the error above. Configure returns to the terminal wizard."
            )
            if action == "doctor" and result == 1:
                progress.update("Checks finished. Follow the fixes above.")
            screen.options(result, action)
            output.scroll_home(animate=False)
        except Exception as error:
            output.write(styled("Error: " + str(error), self.plain))
            progress.update("Stopped. No confirmation was assumed.")
            screen.options(2, action)
        finally:
            self.busy = self.changing = False
            if self.exit_requested:
                self.exit()

    async def stop_read(self):
        if self.busy and not self.changing and self.operation_worker:
            self.operation_worker.cancel()
            try:
                await self.operation_worker.wait()
            except WorkerCancelled:
                pass

    async def stop_dashboard(self):
        """Stop the dashboard worker and wait, so its child process ends before the loop closes."""
        if self.worker and not self.worker.is_finished:
            self.worker.cancel()
            try:
                await self.worker.wait()
            except WorkerCancelled:
                pass

    async def action_back(self):
        if self.uninstalled:
            self.exit()
            return
        if isinstance(self.screen, (Review, ConnectionPicker, CommandForm)):
            self.screen.action_cancel()
            return
        if self.busy and self.changing:
            self.screen.query_one("#progress", Static).update(
                "Wait for this operation to finish. q closes afterward."
            )
            return
        await self.stop_read()
        if len(self.screen_stack) > 1:
            await self.pop_screen()
            if isinstance(self.screen, ActionScreen):
                self.perform_action(self.screen, self.screen.action)
            elif len(self.screen_stack) == 1:
                self.worker = self.load_dashboard()
        else:
            await self.stop_dashboard()
            self.exit()

    async def action_quit(self):
        if self.busy and self.changing:
            self.exit_requested = True
            if isinstance(self.screen, (Review, ConnectionPicker, CommandForm)):
                self.screen.action_cancel()
            else:
                self.screen.query_one("#progress", Static).update(
                    "Will close when this operation finishes."
                )
        else:
            await self.stop_read()
            await self.stop_dashboard()
            self.exit()

    def _fatal_error(self):
        # Textual's default includes locals in a traceback. Keep private machine data off screen.
        self.panic(Text("Error: The terminal interface stopped. Plain Fleet commands still work."))


def main(argv=None):
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from command_display import current_command

        print(
            "Fleet TUI needs a terminal. Run " + current_command() + " --help for plain commands."
        )
        return 0
    app = FleetApp()
    result = app.run()
    if result in ("configure", "machine-install"):
        import command

        arguments = ["configure"] if result == "configure" else ["machines", "install"]
        return style.run_cli(lambda: command.main(arguments), ROOT)
    return app.return_code or 0


if __name__ == "__main__":
    sys.exit(main())
