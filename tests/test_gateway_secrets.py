"""Check that the MCP gateway reads ~/.secrets for the variables each server declares.

MCP means Model Context Protocol. Every test uses a temporary HOME, never the real one.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_gateway_secrets.py --help.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import gateway

CHILD = (
    "import json, os; print(json.dumps({k: os.environ.get(k) for k in ('WANTED', 'OTHER', 'OWN')}))"
)


class Home(unittest.TestCase):
    """Run each test with a temporary HOME and capture the gateway's log lines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        clean = {k: v for k, v in os.environ.items() if k not in ("WANTED", "OTHER", "OWN")}
        patcher = mock.patch.dict(os.environ, {**clean, "HOME": self.tmp.name}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.secrets = Path(self.tmp.name) / ".secrets"

    def write(self, text, mode=0o600):
        self.secrets.write_text(text)
        self.secrets.chmod(mode)

    def read(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            result = gateway.read_secrets()
        return result, buffer.getvalue().splitlines()


class ParsingTest(Home):
    def test_forms_and_quotes(self):
        self.write(
            "# comment\n\nexport A=one\nB=two\nC='a b'\nD=\"c d\"\nrm -rf x\n"
            "E=$(cmd)\n1BAD=x\nF=a b\n"
        )
        result, lines = self.read()
        self.assertEqual(result, {"A": "one", "B": "two", "C": "a b", "D": "c d", "E": "$(cmd)"})
        self.assertEqual(lines, [])

    def test_a_hash_inside_a_value_stays_and_a_trailing_comment_goes(self):
        self.write("A=foo#bar\nB='x # y'\nC=token # note\nD=\"p#q\" #c\n")
        result, _ = self.read()
        self.assertEqual(result, {"A": "foo#bar", "B": "x # y", "C": "token", "D": "p#q"})


class StdioTest(Home):
    def run_child(self, spec):
        os.environ["OWN"] = "from-env"
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            server = gateway.StdioServer(
                "s",
                {"command": sys.executable, "args": ["-c", CHILD], **spec},
                gateway.read_secrets(),
            )
        server.start()
        out = server.process.stdout.read()
        server.close()
        server.process.stdout.close()
        return json.loads(out)

    def test_declared_only_and_env_wins(self):
        self.write("WANTED=secret1\nOTHER=secret2\nOWN=from-file\n")
        seen = self.run_child({"pass_env": ["WANTED", "OWN"]})
        self.assertEqual(seen, {"WANTED": "secret1", "OTHER": None, "OWN": "from-env"})


class HttpTest(Home):
    def test_header_and_bearer_from_secrets(self):
        self.write("HK=header-value\nTOK=token-value\nUNUSED=x\n")
        server = gateway.HttpServer(
            "h",
            {"url": "http://127.0.0.1:1", "header_env": {"X-Key": "HK"}, "bearer_token_env": "TOK"},
            gateway.read_secrets(),
        )
        server.start()
        self.assertEqual(
            server.headers, {"X-Key": "header-value", "Authorization": "Bearer token-value"}
        )

    def test_environment_wins(self):
        self.write("HK=file\n")
        os.environ["HK"] = "env"
        server = gateway.HttpServer(
            "h",
            {"url": "http://127.0.0.1:1", "header_env": {"X-Key": "HK"}},
            gateway.read_secrets(),
        )
        server.start()
        self.assertEqual(server.headers["X-Key"], "env")


class RefusalTest(Home):
    def test_group_readable_refused(self):
        self.write("WANTED=topsecret\n", 0o644)
        result, lines = self.read()
        self.assertEqual(result, {})
        self.assertEqual(len(lines), 1)
        self.assertIn(str(self.secrets), lines[0])
        self.assertNotIn("topsecret", lines[0])

    def test_other_owner_refused(self):
        self.write("WANTED=topsecret\n")
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            result, lines = self.read()
        self.assertEqual(result, {})
        self.assertEqual(len(lines), 1)
        self.assertIn("owns", lines[0])
        self.assertNotIn("topsecret", lines[0])

    def test_missing_file_is_silent(self):
        result, lines = self.read()
        self.assertEqual((result, lines), ({}, []))


if __name__ == "__main__":
    unittest.main()
