"""离线测试，无网络、无 API 密钥。"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_team.core import AgentTeam, choose_specialist
from agent_team.provider import DemoProvider, provider_from_env


class ReviewProvider:
    is_demo = False

    def __init__(self):
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        if "[ROLE:REVIEWER]" in system:
            return "REVISE: 请补充验证方法。"
        return f"结果{self.calls}"


class TestAgentTeam(unittest.TestCase):
    def test_routing(self):
        self.assertEqual(choose_specialist("请写一个 Python 脚本"), "coder")
        self.assertEqual(choose_specialist("阅读这篇论文"), "researcher")
        self.assertEqual(choose_specialist("写一封问候邮件"), "writer")

    def test_demo(self):
        result = AgentTeam(DemoProvider()).run("请帮我写一份计划")
        self.assertTrue(result.demo)
        self.assertFalse(result.revised)
        self.assertIn("演示", result.final)

    def test_review_requests_one_revision(self):
        provider = ReviewProvider()
        result = AgentTeam(provider).run("写一段简介")
        self.assertEqual(provider.calls, 4)
        self.assertTrue(result.revised)
        self.assertEqual(result.final, "结果4")

    def test_empty_task(self):
        with self.assertRaises(ValueError):
            AgentTeam(DemoProvider()).run(" ")

    def test_incomplete_credentials_fail_closed(self):
        with patch.dict(os.environ, {"AGENT_TEAM_API_KEY": "abc", "AGENT_TEAM_MODEL": ""}):
            with self.assertRaises(ValueError):
                provider_from_env()


if __name__ == "__main__":
    unittest.main()
