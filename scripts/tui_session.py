"""Run one existing core action without sharing terminal input or Python globals.

Usage: await CoreSession(root).run(action, output, event, confirm).
Help: python3 scripts/tui_session.py --help.
"""

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys

from ui_channel import CHANNEL


class CoreSession:
    def __init__(self, root):
        self.root = root

    async def run(
        self, action, output, event, confirm, backup=None, machine=None, selected=None, entry=None
    ):
        parent, child = socket.socketpair()
        environment = dict(os.environ, NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
        environment[CHANNEL] = str(child.fileno())
        arguments = [sys.executable, "-u", str(self.root / "scripts/tui_core.py"), action]
        if backup:
            arguments.extend(["--backup", backup])
        if entry:
            arguments.extend(["--entry", entry])
        if machine:
            arguments.extend(["--machine", machine])
        for name in selected or []:
            arguments.extend(["--selected-backup", name])
        process = None
        writer = None
        tasks = []
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                env=environment,
                pass_fds=(child.fileno(),),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                limit=16 * 1024 * 1024,
            )
            child.close()
            reader, writer = await asyncio.open_connection(sock=parent, limit=16 * 1024 * 1024)

            async def read_output():
                while line := await process.stdout.readline():
                    output(line.decode(errors="replace").rstrip("\n"))

            async def read_events():
                while line := await reader.readline():
                    message = json.loads(line)
                    if message["kind"] in ("review", "choose", "form"):
                        answer = await confirm(message)
                        writer.write((json.dumps({"answer": answer}) + "\n").encode())
                        await writer.drain()
                    else:
                        event(message)

            tasks = [asyncio.create_task(read_output()), asyncio.create_task(read_events())]
            await asyncio.gather(*tasks)
            return await process.wait()
        finally:
            child.close()
            try:
                if process and process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if writer:
                    writer.close()
                    await writer.wait_closed()
                else:
                    parent.close()
            finally:
                # Close the subprocess transport now, while the loop runs. A transport left for
                # garbage collection after the loop closes prints "Event loop is closed".
                transport = getattr(process, "_transport", None)
                if transport is not None:
                    transport.close()


def usage():
    import argparse
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
