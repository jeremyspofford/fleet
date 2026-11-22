"""Check that agent items reject core in domains and that items without domains reach every machine.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_domains.py --help.
"""

import tomllib
import unittest

import agent_table
import test_agent_table

MCP = """
overlays = ["core", "personal", "work"]
[agents]
schema = 1
[agents.mcp.docs]
description = "Search the documentation."
transport = "http"
url = "https://mcp.example.com/docs"
"""
SKILL = """
overlays = ["core", "personal", "work"]
[agents]
schema = 1
[agents.skills.release-notes]
"""


class AgentDomainTests(unittest.TestCase):
    setUp = test_agent_table.AgentTableTests.setUp
    tearDown = test_agent_table.AgentTableTests.tearDown
    load = test_agent_table.AgentTableTests.load
    rejects = test_agent_table.AgentTableTests.rejects

    def test_core_alone_fails_naming_the_item(self):
        self.rejects(MCP + 'domains = ["core"]\n', "agents.mcp.docs: core always applies.")
        self.rejects(MCP + 'domains = ["core"]\n', "Omit domains to reach every machine.")

    def test_core_with_another_domain_fails(self):
        self.rejects(MCP + 'domains = ["core", "work"]\n', "core always applies")

    def test_core_in_a_skill_fails(self):
        self.rejects(SKILL + 'domains = ["core"]\n', "agents.skills.release-notes: core always")

    def test_other_domains_still_pass(self):
        table = self.load(MCP + 'domains = ["work"]\n')
        self.assertEqual(table.items[0].spec["domains"], ["work"])

    def test_item_without_domains_applies_on_every_domain(self):
        item = self.load(MCP).items[0]
        for domain in ("core", "work", "personal"):
            self.assertTrue(agent_table.applies(item, {"domain": domain}), domain)


if __name__ == "__main__":
    unittest.main()
