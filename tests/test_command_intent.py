"""Natural-language preview, authorization, worker permission and execution tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_team.command_intent import IntentError, interpret
from agent_team.desktop_worker import WorkerError, execute
from agent_team.server import create_app


class IntentTests(unittest.TestCase):
    def test_supported_intents_are_explicit_and_preview_only(self):
        cases = {
            "请查看电脑状态": ("system_info", {}),
            "列出工作目录": ("list_files", {"path": "."}),
            "列出目录 src": ("list_files", {"path": "src"}),
            "读取 README.md": ("read_text", {"path": "README.md"}),
            "搜索关键词 TODO": ("search_text", {"query": "TODO", "path": "."}),
            "运行单元测试": ("run_tests", {}),
            "保存便签：检查报告": ("write_note", {"text": "检查报告"}),
            "通知电脑：开始工作": ("notify", {"text": "开始工作"}),
        }
        for text, expected in cases.items():
            with self.subTest(command=text):
                planned = interpret(text)
                self.assertEqual((planned["action"], planned["payload"]), expected)
                self.assertTrue(planned["requires_confirmation"])
                self.assertNotIn("task_id", planned)

    def test_untrusted_or_ambiguous_instruction_not_guessed(self):
        for command in ("帮我把项目修好", "删除整个项目", "运行命令 rm -rf /",
                        "运行单元测试然后删除文件", "", "x" * 501):
            with self.subTest(command=command[:20]), self.assertRaises(IntentError):
                interpret(command)


class IntentAPITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"AGENT_TEAM_ACCESS_TOKEN": "my-safe-owner-token"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(create_app(tmp.name))
        self.owner = {"X-Agent-Team-Token": "my-safe-owner-token"}
        device = self.client.post("/api/devices", json={"name": "laptop"}, headers=self.owner)
        self.assertEqual(device.status_code, 201)
        self.device = device.json()
        self.url = f"/api/devices/{self.device['id']}"

    def test_preview_requires_owner_and_never_queues(self):
        for headers in ({}, {"Authorization": "Bearer " + self.device["device_token"]}):
            self.assertEqual(self.client.post(self.url + "/interpret", headers=headers,
                                              json={"text": "运行单元测试"}).status_code, 401)
        res = self.client.post(self.url + "/interpret", headers=self.owner,
                               json={"text": "运行单元测试"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["action"], "run_tests")
        self.assertEqual(self.client.get(self.url + "/tasks", headers=self.owner).json()["tasks"], [])
        queued = self.client.post(self.url + "/tasks", headers=self.owner,
                                  json={"action": res.json()["action"], "payload": res.json()["payload"]})
        self.assertEqual(queued.status_code, 201)
        self.assertEqual(self.client.get(self.url + "/tasks", headers=self.owner).json()["tasks"][0]["action"], "run_tests")

    def test_phone_preview_to_desktop_execution_and_sync(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "README.md").write_text("TODO: test phone to desktop flow", encoding="utf-8")
            proposal = self.client.post(self.url + "/interpret", headers=self.owner,
                                        json={"text": "搜索关键词 TODO"}).json()
            task = self.client.post(self.url + "/tasks", headers=self.owner,
                                    json={"action": proposal["action"],
                                          "payload": proposal["payload"]})
            self.assertEqual(task.status_code, 201)
            worker_headers = {"Authorization": "Bearer " + self.device["device_token"]}
            claimed = self.client.get("/api/device/jobs/next", headers=worker_headers).json()["task"]
            self.assertEqual(claimed["id"], task.json()["id"])
            result = execute(claimed["action"], claimed["payload"], root)
            report = self.client.post(f"/api/device/jobs/{claimed['id']}/result",
                                      headers=worker_headers, json={"ok": True, "result": result})
            self.assertEqual(report.status_code, 200)
            synced = self.client.get(self.url + "/tasks", headers=self.owner).json()["tasks"][0]
            self.assertEqual(synced["status"], "completed")
            self.assertEqual(json.loads(synced["result"])["matches"][0]["file"], "README.md")

    def test_unknown_intent_and_invalid_execution_not_queued(self):
        self.assertEqual(self.client.post(self.url + "/interpret", headers=self.owner,
                                          json={"text": "清空硬盘"}).status_code, 422)
        self.assertEqual(self.client.post(self.url + "/tasks", headers=self.owner,
                                          json={"action": "search_text", "payload": {"query": ""}}).status_code, 400)
        self.assertEqual(self.client.post(self.url + "/tasks", headers=self.owner,
                                          json={"action": "run_tests", "payload": {"command": "echo hi"}}).status_code, 400)
        self.assertEqual(self.client.get(self.url + "/tasks", headers=self.owner).json()["tasks"], [])
        self.assertEqual(self.client.post("/api/devices/missing/interpret", headers=self.owner,
                                          json={"text": "查看电脑状态"}).status_code, 404)


class WorkerPermissionTests(unittest.TestCase):
    def test_text_search_stays_inside_authorized_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("# TODO: add code\nprint('ok')\n", encoding="utf-8")
            (root / ".credentials.py").write_text("# TODO secret", encoding="utf-8")
            (root / "large.py").write_text("# TODO large\n" + "x" * 121000, encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "report.md").write_text("todo in report\n", encoding="utf-8")
            try:
                (root / "link.py").symlink_to(root / ".credentials.py")
            except OSError:
                pass
            data = json.loads(execute("search_text", {"query": "TODO", "path": "."}, root))
            self.assertEqual({item["file"] for item in data["matches"]}, {"app.py", "docs/report.md"})
            self.assertNotIn(".credentials", json.dumps(data))
            with self.assertRaises(WorkerError):
                execute("search_text", {"query": "TODO", "path": ".."}, root)

    def test_run_tests_requires_local_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_small.py").write_text(
                "import unittest, os\n"
                "class GoodTest(unittest.TestCase):\n"
                "    def test_safe(self):\n"
                "        self.assertNotIn('AGENT_TEAM_DEVICE_TOKEN', os.environ)\n", encoding="utf-8")
            with self.assertRaises(WorkerError):
                execute("run_tests", {}, root)
            with patch.dict(os.environ, {"AGENT_TEAM_DEVICE_TOKEN": "secret-example"}):
                response = execute("run_tests", {}, root, allow_tests=True)
            self.assertIn("退出码：0", response)
            self.assertIn("Ran 1 test", response)

    def test_run_tests_failed_test_is_reported_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_fail.py").write_text(
                "import unittest\n"
                "class BadTest(unittest.TestCase):\n"
                "    def test_no(self):\n"
                "        self.fail('expected test failure')\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkerError, "退出码：1"):
                execute("run_tests", {}, root, allow_tests=True)
            with self.assertRaises(WorkerError):
                execute("shell", {"command": "echo hi"}, root, allow_tests=True)


if __name__ == "__main__":
    unittest.main()
