"""Serve every declared MCP server to one agent through a single gateway entry.

MCP means Model Context Protocol. An MCP server offers tools, prompts, and resources to an agent.
The gateway speaks MCP over standard input and output. It starts each downstream server on demand.
It serves their tools and prompts under SERVER__NAME, and their resources for reading only.

Usage: python3 scripts/gateway.py --config FILE
Parameters: --config names the agent's gateway file in the Fleet tree.
Agents start this command themselves. Diagnostics go to standard error, never to the agent's channel.
"""

# Exit codes: 0 after the agent closes the channel; 2 invalid arguments.

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
SEPARATOR = "__"
NAME_LIMIT = 64
START_TIMEOUT = 30


def log(message):
    print("fleet-gateway: " + message, file=sys.stderr, flush=True)


class RemoteError(Exception):
    """A downstream server answered with a JSON-RPC error."""

    def __init__(self, error):
        super().__init__(error.get("message", "downstream error"))
        self.error = error


def expand(text, environment):
    """Replace ${NAME} with the variable's value. A missing variable becomes empty."""
    return re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: environment.get(m.group(1), ""), text
    )


SECRET_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")


def read_secrets():
    """Read NAME=VALUE lines from ~/.secrets. Refuse the file when others can read or own it."""
    path = Path.home() / ".secrets"
    try:
        info = path.stat()
        if info.st_mode & 0o077:
            reason = "other users can read it"
        elif info.st_uid != os.getuid():
            reason = "another user owns it"
        else:
            reason = None
        text = None if reason else path.read_text()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as error:
        reason, text = f"cannot read it ({error.__class__.__name__})", None
    if text is None:
        log(f"ignoring {path}: {reason}")
        return {}
    secrets = {}
    for line in text.splitlines():
        found = SECRET_LINE.fullmatch(line.strip())
        if not found:
            continue
        try:
            words = shlex.split(found.group(2), comments=True)  # Quoting only; no shell runs.
        except ValueError:
            continue
        if len(words) <= 1:
            secrets[found.group(1)] = words[0] if words else ""
    return secrets


def lookup(secrets, names):
    """Return the named variables. The gateway's own environment wins over secrets."""
    return {
        name: os.environ[name] if name in os.environ else secrets[name]
        for name in names
        if name in os.environ or name in secrets
    }


class StdioServer:
    """One downstream server that runs as a local program."""

    def __init__(self, name, spec, secrets=None):
        self.name, self.spec = name, spec
        self.secrets = secrets or {}
        self.process = None
        self.counter = 0
        self.lock = threading.Lock()

    def start(self):
        environment = dict(os.environ)
        environment.update(lookup(self.secrets, self.spec.get("pass_env", [])))
        environment.update(self.spec.get("env", {}))
        self.process = subprocess.Popen(
            [self.spec["command"], *self.spec.get("args", [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            env=environment,
            text=True,
            bufsize=1,
        )

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        with self.lock:
            self.counter += 1
            identity = self.counter
            message = {"jsonrpc": "2.0", "id": identity, "method": method}
            if params is not None:
                message["params"] = params
            self.send(message)
            while True:
                line = self.process.stdout.readline()
                if not line:
                    raise ConnectionError(f"{self.name} closed its channel")
                incoming = json.loads(line)
                if "method" in incoming and "id" in incoming:
                    # The gateway offers no sampling, roots, or elicitation to downstream servers.
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": incoming["id"],
                            "error": {"code": -32601, "message": "Not offered by Fleet's gateway"},
                        }
                    )
                    continue
                if incoming.get("id") != identity:
                    continue  # Notifications and stray replies carry no answer for this request.
                if "error" in incoming:
                    raise RemoteError(incoming["error"])
                return incoming.get("result", {})

    def notify(self, method):
        with self.lock:
            self.send({"jsonrpc": "2.0", "method": method})

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class HttpServer:
    """One downstream server reached over the Streamable HTTP transport."""

    def __init__(self, name, spec, secrets=None):
        self.name, self.spec = name, spec
        self.secrets = secrets or {}
        self.session = None
        self.protocol = None
        self.counter = 0
        self.lock = threading.Lock()

    def start(self):
        declared = [*self.spec.get("header_env", {}).values()]
        declared += [self.spec["bearer_token_env"]] if self.spec.get("bearer_token_env") else []
        environment = {**dict(os.environ), **lookup(self.secrets, declared)}
        headers = {
            key: expand(value, environment) for key, value in self.spec.get("headers", {}).items()
        }
        for header, variable in self.spec.get("header_env", {}).items():
            headers[header] = environment.get(variable, "")
        if self.spec.get("bearer_token_env"):
            headers["Authorization"] = "Bearer " + environment.get(
                self.spec["bearer_token_env"], ""
            )
        self.headers = headers

    def post(self, message):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        request = urllib.request.Request(
            self.spec["url"], data=json.dumps(message).encode(), headers=headers, method="POST"
        )
        return urllib.request.urlopen(
            request, timeout=START_TIMEOUT if "id" not in message else None
        )

    def request(self, method, params=None):
        with self.lock:
            self.counter += 1
            identity = self.counter
            message = {"jsonrpc": "2.0", "id": identity, "method": method}
            if params is not None:
                message["params"] = params
            try:
                response = self.post(message)
            except urllib.error.HTTPError as error:
                raise ConnectionError(f"{self.name} answered HTTP {error.code}") from error
            with response:
                self.session = response.headers.get("Mcp-Session-Id") or self.session
                kind = response.headers.get("Content-Type", "")
                answers = (
                    sse_messages(response) if "text/event-stream" in kind else [json.load(response)]
                )
                for incoming in answers:
                    if incoming.get("id") != identity:
                        continue
                    if "error" in incoming:
                        raise RemoteError(incoming["error"])
                    return incoming.get("result", {})
            raise ConnectionError(f"{self.name} sent no answer for {method}")

    def notify(self, method):
        with self.lock:
            try:
                self.post({"jsonrpc": "2.0", "method": method}).close()
            except urllib.error.HTTPError as error:
                if error.code not in (202, 204):
                    raise ConnectionError(f"{self.name} answered HTTP {error.code}") from error

    def close(self):
        pass


def sse_messages(stream):
    """Yield JSON-RPC messages from a Server-Sent Events body."""
    data = []
    for raw in stream:
        line = raw.decode().rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


def served_name(server, name, taken):
    """Join a server and item name into a valid, unique tool or prompt name."""
    raw = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}{SEPARATOR}{name}")
    if len(raw) > NAME_LIMIT:
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        raw = raw[: NAME_LIMIT - 9] + "_" + digest
    while raw in taken:
        raw = raw[: NAME_LIMIT - 2] + "_x"
    return raw


class Gateway:
    def __init__(self, config):
        self.servers = {}
        secrets = read_secrets()  # Read once per gateway start.
        for name, spec in sorted(config.get("servers", {}).items()):
            kind = StdioServer if spec.get("transport", "stdio") == "stdio" else HttpServer
            self.servers[name] = kind(name, spec, secrets)
        self.ready = {}
        self.tools, self.prompts, self.resources = {}, {}, {}

    def connect(self, name):
        """Start and initialize one downstream server once. A failure skips that server."""
        if name in self.ready:
            return self.ready[name]
        server = self.servers[name]
        try:
            server.start()
            result = server.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "fleet-gateway", "version": version()},
                },
            )
            if isinstance(server, HttpServer):
                server.protocol = result.get("protocolVersion", PROTOCOL)
            server.notify("notifications/initialized")
            self.ready[name] = result.get("capabilities", {})
        except (OSError, ValueError, ConnectionError, RemoteError) as error:
            log(f"{name} did not start: {error}")
            self.ready[name] = None
        return self.ready[name]

    def collect(self, name, method, key):
        """Read every page of one list method from one server."""
        items, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.servers[name].request(method, params)
            items.extend(result.get(key, []))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    def list_tools(self):
        self.tools, result = {}, []
        for name in self.servers:
            capabilities = self.connect(name)
            if not capabilities or "tools" not in capabilities:
                continue
            try:
                tools = self.collect(name, "tools/list", "tools")
            except (OSError, ValueError, ConnectionError, RemoteError) as error:
                log(f"{name} tools/list failed: {error}")
                continue
            for tool in tools:
                served = served_name(name, tool["name"], self.tools)
                self.tools[served] = (name, tool["name"])
                result.append(dict(tool, name=served))
        return {"tools": result}

    def list_prompts(self):
        self.prompts, result = {}, []
        for name in self.servers:
            capabilities = self.connect(name)
            if not capabilities or "prompts" not in capabilities:
                continue
            try:
                prompts = self.collect(name, "prompts/list", "prompts")
            except (OSError, ValueError, ConnectionError, RemoteError) as error:
                log(f"{name} prompts/list failed: {error}")
                continue
            for prompt in prompts:
                served = served_name(name, prompt["name"], self.prompts)
                self.prompts[served] = (name, prompt["name"])
                result.append(dict(prompt, name=served))
        return {"prompts": result}

    def list_resources(self):
        self.resources, result = {}, []
        for name in self.servers:
            capabilities = self.connect(name)
            if not capabilities or "resources" not in capabilities:
                continue
            try:
                resources = self.collect(name, "resources/list", "resources")
            except (OSError, ValueError, ConnectionError, RemoteError) as error:
                log(f"{name} resources/list failed: {error}")
                continue
            for resource in resources:
                if resource["uri"] in self.resources:
                    log(f"{name} repeats resource {resource['uri']}; the first server keeps it")
                    continue
                self.resources[resource["uri"]] = name
                result.append(resource)
        return {"resources": result}

    def route(self, attribute, refresh, served):
        """Find the server for a served name. Refresh the list once when the name is new."""
        if served not in getattr(self, attribute):
            refresh()
        table = getattr(self, attribute)
        if served not in table:
            raise RemoteError({"code": -32602, "message": f"Unknown name: {served}"})
        return table[served]

    def handle(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion", PROTOCOL)
            return {
                "protocolVersion": requested if requested in SUPPORTED else PROTOCOL,
                "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                "serverInfo": {"name": "fleet", "version": version()},
                "instructions": "Fleet serves every declared MCP server as SERVER__NAME.",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return self.list_tools()
        if method == "tools/call":
            server, name = self.route("tools", self.list_tools, params.get("name"))
            return self.servers[server].request("tools/call", dict(params, name=name))
        if method == "prompts/list":
            return self.list_prompts()
        if method == "prompts/get":
            server, name = self.route("prompts", self.list_prompts, params.get("name"))
            return self.servers[server].request("prompts/get", dict(params, name=name))
        if method == "resources/list":
            return self.list_resources()
        if method == "resources/read":
            server = self.route("resources", self.list_resources, params.get("uri"))
            return self.servers[server].request("resources/read", params)
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        raise RemoteError({"code": -32601, "message": f"Method not found: {method}"})

    def serve(self, reader, writer):
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                reply(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    },
                )
                continue
            if not isinstance(message, dict):
                error = {"code": -32600, "message": "Invalid request"}
                reply(writer, {"jsonrpc": "2.0", "id": None, "error": error})
                continue
            if "id" not in message:
                continue  # Notifications from the agent need no answer.
            try:
                result = self.handle(message.get("method", ""), message.get("params") or {})
                reply(writer, {"jsonrpc": "2.0", "id": message["id"], "result": result})
            except RemoteError as error:
                reply(writer, {"jsonrpc": "2.0", "id": message["id"], "error": error.error})
            except (OSError, ValueError, ConnectionError) as error:
                reply(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32603, "message": str(error)},
                    },
                )
        for server in self.servers.values():
            server.close()


def reply(writer, message):
    writer.write(json.dumps(message) + "\n")
    writer.flush()


def version():
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "0"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, required=True, help="agent gateway file in the tree")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
    except (OSError, ValueError) as error:
        log(f"cannot read {args.config}: {error}")
        config = {"servers": {}}
    Gateway(config).serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
