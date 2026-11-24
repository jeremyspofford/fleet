"""Run chezmoi apply on its own terminal, explain its conflict menus, and record conflict keys.

Usage: python3 scripts/apply_terminal.py
apply.sh runs this when a terminal is present. chezmoi gets a pseudo-terminal of its own.
Content scripts then read passwords, prompts, and /dev/tty as they would without Fleet.
The user's terminal is raw while chezmoi runs and is restored on exit, Ctrl-C, or a signal.
FLEET_APPLY_CHOICES names a file. Each answer to a conflict menu adds one line to it:
the chosen menu word, a tab, then chezmoi's question. Free text is never recorded.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
"""

# Exit codes: chezmoi's own exit code; 128 plus the signal number when a signal stops it.

import errno
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios

# chezmoi --no-tty asks: QUESTION (CHOICE/CHOICE/...)? and reads one line.
MENU = re.compile(r"^(?P<question>.*) \((?P<choices>[A-Za-z]+(?:[/-][A-Za-z]+)*)\)\? $")
CONFLICT_CHOICES = {
    "diff/overwrite/all-overwrite/skip/Skip-conflicts/quit",
    "overwrite/all-overwrite/skip/Skip-conflicts/quit",
    "diff/overwrite/all-overwrite/skip/quit",
    "overwrite/all-overwrite/skip/quit",
    "diff/yes/no/all/quit",
    "yes/no/all/quit",
}
KEYS = {
    "diff": ("d", "Show changes for this file, then ask again."),
    "overwrite": ("o", "Overwrite this file with the generated version."),
    "all-overwrite": ("a", "Overwrite this file and all remaining conflicts without asking again."),
    "skip": ("s", "Keep this file unchanged; continue with other files. Recommended if unsure."),
    "Skip-conflicts": ("S", "Keep all conflicting files unchanged; apply other changes."),
    "yes": ("y", "Apply the proposed change to this file."),
    "no": ("n", "Keep this file unchanged; continue with other files. Recommended if unsure."),
    "all": ("a", "Apply remaining changes, but still ask about conflicts."),
    "quit": ("q", "Stop applying. Changes already made remain in place."),
}
# A partial line shows after this many seconds without output: a prompt may wait for input.
QUIET = 0.2


def resolve(answer, choices):
    """Return the menu word that chezmoi 2.74.1 reads ANSWER as, or None.

    chezmoi trims the line, then accepts a choice or any prefix that only one choice has.
    So S, Sk, Skip, and Skip-conflicts all mean Skip-conflicts.
    """
    answer = answer.strip()
    if answer in choices:
        return answer
    matches = [choice for choice in choices if choice.startswith(answer)] if answer else []
    return matches[0] if len(matches) == 1 else None


def explain(question, choices):
    """Return the explained menu that replaces chezmoi's one-line question."""
    lines = ["", "File conflict", "-------------", question + "."]
    lines.append("Type one letter, then press Enter. Enter alone asks again.")
    if "Skip-conflicts" in choices:
        lines.append("Uppercase S differs from lowercase s.")
    lines.append("")
    lines += [f"  {KEYS[choice][0]}  {KEYS[choice][1]}" for choice in choices]
    lines += ["", "Choose [" + "/".join(KEYS[choice][0] for choice in choices) + "]: "]
    return "\n".join(lines)


class Relay:
    """Pass bytes between the user's terminal and chezmoi's, and record conflict answers."""

    def __init__(self, terminal, master, slave, choices_file):
        self.terminal, self.master, self.slave = terminal, master, slave
        self.choices_file = choices_file
        self.line = b""  # chezmoi output since the last newline, not yet shown
        self.typed = b""  # user input since the last Enter
        self.menu = None  # (question, choices, clean) while a conflict menu waits
        # True once anything is typed while no menu waits. chezmoi may read it as the next
        # answer, so the next menu's answer is not recorded. Answering a menu clears it.
        self.ahead = False
        # A terminal answers chezmoi's queries with escape sequences. They are not typing.
        self.escape = None

    def show(self, data):
        os.write(self.terminal, data)

    def output(self, data):
        """Show chezmoi output. Replace a conflict menu with its explanation."""
        self.line += data
        while b"\n" in self.line:
            shown, self.line = self.line.split(b"\n", 1)
            self.show(shown + b"\n")
        self.check_menu()

    def check_menu(self):
        text = self.line.decode(errors="replace")
        found = MENU.match(text)
        if not found or found["choices"] not in CONFLICT_CHOICES:
            return
        choices = found["choices"].split("/")
        # Input typed before the menu may already wait in chezmoi's queue. Never record it.
        waiting = struct.unpack("i", fcntl.ioctl(self.slave, termios.FIONREAD, b"\0\0\0\0"))[0]
        clean = not self.ahead and not self.typed and not waiting
        self.menu = (found["question"], choices, clean)
        self.line = b""
        self.show(explain(found["question"], choices).replace("\n", "\r\n").encode())

    def flush_quiet(self):
        """Show a partial line after a quiet moment, such as a script's own prompt."""
        if self.line:
            self.show(self.line)
            self.line = b""

    def input(self, data):
        """Pass user input to chezmoi. Record the line that answers a conflict menu."""
        os.write(self.master, data)
        for byte in data:
            if self.in_escape(byte):
                continue
            if self.menu is None:
                self.ahead = True
            if byte in (0x0D, 0x0A):
                self.answered()
                self.typed = b""
            elif byte in (0x7F, 0x08):
                self.typed = self.typed[:-1]
            else:
                self.typed += bytes([byte])

    def in_escape(self, byte):
        """Return True while BYTE belongs to a terminal escape sequence, such as ESC [ 1 ; 1 R."""
        state = self.escape
        if state is None:
            if byte == 0x1B:
                self.escape = "start"
                return True
            return False
        if state == "start":
            self.escape = {0x5B: "csi", 0x5D: "string", 0x50: "string", 0x4F: "one"}.get(byte)
            if byte in (0x58, 0x5E, 0x5F):
                self.escape = "string"
        elif state == "csi":
            if 0x40 <= byte <= 0x7E:
                self.escape = None
        elif state == "string":
            # A string ends with BEL or with ESC backslash.
            self.escape = None if byte == 0x07 else ("string-end" if byte == 0x1B else "string")
        elif state == "string-end":
            self.escape = None if byte == 0x5C else "string"
        else:
            self.escape = None
        return True

    def answered(self):
        if self.menu is None:
            return
        question, choices, clean = self.menu
        self.menu, self.ahead = None, False
        if not clean:
            key = "?"
        else:
            key = resolve(self.typed.decode(errors="replace"), choices)
            if key is None:
                return  # chezmoi asks again, and that new menu is recorded instead
        if self.choices_file:
            with open(self.choices_file, "a") as stream:
                stream.write(f"{key}\t{question}\n")


def window_size(source, target):
    try:
        size = fcntl.ioctl(source, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(target, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def raw_without_echo(attributes):
    """Return terminal attributes with line editing, echo, signals, and output changes off.

    chezmoi's own terminal already ends its lines with a carriage return and a newline.
    """
    raw = list(attributes)
    raw[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    raw[1] &= ~termios.OPOST
    raw[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    raw[6] = list(raw[6])
    raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0
    return raw


def run(command, choices_file=None):
    """Run COMMAND on its own pseudo-terminal. Return its exit code."""
    terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    saved = termios.tcgetattr(terminal)
    master, slave = pty.openpty()
    termios.tcsetattr(slave, termios.TCSANOW, saved)
    window_size(terminal, slave)

    def controlling_terminal():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        for number in (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, signal.SIG_DFL)

    process = subprocess.Popen(
        command, stdin=slave, stdout=slave, stderr=slave, preexec_fn=controlling_terminal
    )
    relay = Relay(terminal, master, slave, choices_file)

    def restore(*_):
        termios.tcsetattr(terminal, termios.TCSADRAIN, saved)

    def stop(number, _frame):
        restore()
        process.send_signal(signal.SIGHUP)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        os._exit(128 + number)

    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, stop)
    signal.signal(signal.SIGWINCH, lambda *_: window_size(terminal, slave))
    try:
        termios.tcsetattr(terminal, termios.TCSADRAIN, raw_without_echo(saved))
        while True:
            try:
                ready, _, _ = select.select([terminal, master], [], [], QUIET)
            except InterruptedError:
                continue
            if master in ready:
                try:
                    relay.output(os.read(master, 65536))
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
            elif process.poll() is not None:
                break  # chezmoi has ended and its last output has been read
            else:
                relay.flush_quiet()
            if terminal in ready:
                data = os.read(terminal, 4096)
                if data:
                    relay.input(data)
        relay.flush_quiet()
    finally:
        restore()
        for descriptor in (master, slave, terminal):
            os.close(descriptor)
    code = process.wait()
    return 128 - code if code < 0 else code


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    if argv[1:]:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    choices = os.environ.get("FLEET_APPLY_CHOICES") or None
    return run(["chezmoi", "--no-tty", "--no-pager", "apply"], choices and Path(choices))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
