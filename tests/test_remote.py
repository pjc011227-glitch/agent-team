"""Security and end-to-end relay tests for the remote device prototype."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_team.desktop_worker import WorkerError, execute, validate_server_url, workspace_path
from agent_team.remote import RemoteError, claim, enqueue, finish, heartbeat, initialize, register, revoke
from agent_team.server import create_app
from agent_team.storage import Store


class RelayAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.dict(os.environ, {"AGENT_TEAM_ACCESS_TOKEN": "very-private-owner-token"})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = TestClient(create_app(self.tmp.name))
        self.owner = {"X-Agent-Team-Token": "very-private-owner-token"}

    def pair(self, name="desktop"):
        res = self.client.post("/api/devices", json={"name": name}, headers=self.owner)
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def worker_headers(self, item):
        return {"Authorization": f"Bearer {item['device_token']}"}

    def test_pair_and_revoke_tokens_do_not_leak(self):
        device = self.pair()
        result = self.client.get("/api/devices", headers=self.owner)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(device["device_token"], result.text)
        self.assertNotIn("token_hash", result.text)
        self.assertEqual(self.client.get("/api/devices").status_code, 401)
        self.assertEqual(self.client.get("/api/state", headers=self.worker_headers(device)).status_code, 401)
        self.assertEqual(self.client.post("/api/device/heartbeat", headers=self.worker_headers(device),
                                          json={"platform": "Windows"}).status_code, 200)
        self.assertTrue(self.client.get("/api/devices", headers=self.owner).json()["devices"][0]["last_seen_at"])
        self.assertEqual(self.client.delete(f"/api/devices/{device['id']}", headers=self.owner).status_code, 200)
        self.assertEqual(self.client.get("/api/device/jobs/next", headers=self.worker_headers(device)).status_code, 401)

    def test_queue_claim_complete_owner_can_read(self):
        device = self.pair()
        url = f"/api/devices/{device['id']}/tasks"
        task = self.client.post(url, headers=self.owner,
                                json={"action": "notify", "payload": {"text": "你好电脑"}})
        self.assertEqual(task.status_code, 201, task.text)
        self.assertEqual(self.client.get(url).status_code, 401)
        headers = self.worker_headers(device)
        job = self.client.get("/api/device/jobs/next", headers=headers).json()["task"]
        self.assertEqual(job["payload"]["text"], "你好电脑")
        self.assertEqual(job["id"], task.json()["id"])
        self.assertIsNone(self.client.get("/api/device/jobs/next", headers=headers).json()["task"])
        done = self.client.post(f"/api/device/jobs/{job['id']}/result", headers=headers,
                                json={"ok": True, "result": "收到"})
        self.assertEqual(done.status_code, 200)
        listing = self.client.get(url, headers=self.owner).json()["tasks"]
        self.assertEqual(listing[0]["status"], "completed")
        self.assertEqual(listing[0]["result"], "收到")
        self.assertEqual(self.client.post(f"/api/device/jobs/{job['id']}/result", headers=headers,
                                          json={"ok": True, "result": "again"}).status_code, 404)

    def test_device_cannot_take_or_complete_another_devices_tasks(self):
        a, b = self.pair("a"), self.pair("b")
        task = self.client.post(f"/api/devices/{a['id']}/tasks", headers=self.owner,
                                json={"action": "system_info"}).json()
        self.assertIsNone(self.client.get("/api/device/jobs/next", headers=self.worker_headers(b)).json()["task"])
        self.assertEqual(self.client.post(f"/api/device/jobs/{task['id']}/result",
                                          headers=self.worker_headers(b),
                                          json={"ok": True, "result": "x"}).status_code, 404)
        self.assertEqual(self.client.get("/api/device/jobs/next", headers=self.worker_headers(a)).json()["task"]["id"], task["id"])
        self.assertEqual(self.client.post(f"/api/device/jobs/{task['id']}/result",
                                          headers=self.worker_headers(b),
                                          json={"ok": True, "result": "x"}).status_code, 404)

    def test_owner_can_cancel_queued_then_explicitly_retry(self):
        d = self.pair()
        url = f"/api/devices/{d['id']}/tasks"
        first = self.client.post(url, headers=self.owner,
                                 json={"action": "notify", "payload": {"text": "Review the plan"}}).json()
        cancelled = self.client.post(f"{url}/{first['id']}/cancel", headers=self.owner)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertIsNone(self.client.get("/api/device/jobs/next", headers=self.worker_headers(d)).json()["task"])
        self.assertEqual(self.client.post(f"{url}/{first['id']}/cancel", headers=self.owner).status_code, 409)
        retry = self.client.post(f"{url}/{first['id']}/retry", headers=self.owner)
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertNotEqual(retry.json()["id"], first["id"])
        rows = self.client.get(url, headers=self.owner).json()["tasks"]
        self.assertEqual(rows[0]["retry_of"], first["id"])
        self.assertEqual(rows[1]["status"], "cancelled")

    def test_running_cannot_be_cancelled_and_completion_cannot_be_retried(self):
        d = self.pair()
        url = f"/api/devices/{d['id']}/tasks"
        task = self.client.post(url, headers=self.owner,
                                json={"action": "system_info"}).json()
        worker = self.worker_headers(d)
        job = self.client.get("/api/device/jobs/next", headers=worker).json()["task"]
        self.assertEqual(job["id"], task["id"])
        self.assertEqual(self.client.post(f"{url}/{task['id']}/cancel", headers=self.owner).status_code, 409)
        self.assertEqual(self.client.post(f"/api/device/jobs/{task['id']}/result", headers=worker,
                                          json={"ok": True, "result": "ok"}).status_code, 200)
        self.assertEqual(self.client.post(f"{url}/{task['id']}/retry", headers=self.owner).status_code, 409)

    def test_stale_claim_is_interrupted_not_auto_executed(self):
        d = self.pair()
        url = f"/api/devices/{d['id']}/tasks"
        task = self.client.post(url, headers=self.owner,
                                json={"action": "write_note", "payload": {"text": "important"}}).json()
        worker = self.worker_headers(d)
        self.client.get("/api/device/jobs/next", headers=worker)
        from agent_team.remote import mark_interrupted
        count = mark_interrupted(self.client.app.state.store, timeout_seconds=-1)
        self.assertEqual(count, 1)
        self.assertEqual(self.client.get(url, headers=self.owner).json()["tasks"][0]["status"], "interrupted")
        self.assertIsNone(self.client.get("/api/device/jobs/next", headers=worker).json()["task"])
        self.assertEqual(self.client.post(f"/api/device/jobs/{task['id']}/result", headers=worker,
                                          json={"ok": True, "result": "late"}).status_code, 404)
        self.assertEqual(self.client.post(f"{url}/{task['id']}/retry", headers=self.owner).status_code, 201)

    def test_cannot_change_tasks_without_owner_token(self):
        d = self.pair()
        url = f"/api/devices/{d['id']}/tasks"
        task = self.client.post(url, headers=self.owner,
                                json={"action": "system_info"}).json()
        worker = self.worker_headers(d)
        for endpoint in (f"{url}/{task['id']}/cancel", f"{url}/{task['id']}/retry"):
            self.assertEqual(self.client.post(endpoint).status_code, 401)
            self.assertEqual(self.client.post(endpoint, headers=worker).status_code, 401)
            self.assertEqual(self.client.post(endpoint, headers=self.owner).status_code,
                             200 if endpoint.endswith('/cancel') else 201)

    def test_existing_database_schema_is_upgraded(self):
        from agent_team.remote import initialize
        store = self.client.app.state.store
        with store.connection() as db:
            db.execute("ALTER TABLE remote_tasks RENAME TO old_remote_tasks")
            db.execute("""CREATE TABLE remote_tasks (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, action TEXT NOT NULL,
                payload_json TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)""")
            db.execute("DROP TABLE old_remote_tasks")
        initialize(store)
        with store.connection() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(remote_tasks)')}
        self.assertIn('retry_of', columns)

    def test_invalid_job_rejected(self):
        d = self.pair()
        url = f"/api/devices/{d['id']}/tasks"
        self.assertEqual(self.client.post(url, headers=self.owner,
                                          json={"action": "shell", "payload": {"cmd": "whoami"}}).status_code, 422)
        self.assertEqual(self.client.post(url, headers=self.owner,
                                          json={"action": "notify", "payload": {"text": ""}}).status_code, 400)
        self.assertEqual(self.client.post(url, headers=self.owner,
                                          json={"action": "notify", "payload": {"text": "a" * 8001}}).status_code, 400)
        self.assertEqual(self.client.post(url, headers=self.owner,
                                          json={"action": "list_files", "payload": {"path": 3}}).status_code, 400)

    def test_remote_owner_requires_nonempty_secret(self):
        with patch.dict(os.environ, {"AGENT_TEAM_ACCESS_TOKEN": ""}):
            client = TestClient(create_app(Path(self.tmp.name) / "no-token"))
            self.assertEqual(client.get("/api/devices").status_code, 503)
            self.assertEqual(client.post("/api/devices", json={"name": "bad"}).status_code, 503)


class DesktopWorkerTests(unittest.TestCase):
    def test_read_text_only_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.md").write_text("项目进度：完成第一版", encoding="utf-8")
            (root / "secret.bin").write_bytes(b"private")
            (root / "too-big.txt").write_text("a" * 12001)
            self.assertIn("项目进度", execute("read_text", {"path": "report.md"}, root))
            for name in ["secret.bin", "too-big.txt", "missing.txt"]:
                with self.subTest(name=name), self.assertRaises(WorkerError):
                    execute("read_text", {"path": name}, root)
            with self.assertRaises(WorkerError):
                execute("read_text", {"path": "../report.md"}, root)

    def test_symlink_inside_workspace_cannot_expose_hidden_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".secret.txt").write_text("password")
            alias = root / "friendly.txt"
            try:
                alias.symlink_to(root / ".secret.txt")
            except (OSError, NotImplementedError):
                self.skipTest("符号链接创建被操作系统权限策略限制")
            with self.assertRaises(WorkerError):
                execute("read_text", {"path": "friendly.txt"}, root)
            with self.assertRaises(WorkerError):
                workspace_path(root, "friendly.txt")

    def test_validated_server_only_https(self):
        self.assertEqual(validate_server_url("https://relay.example.net"), "https://relay.example.net")
        self.assertEqual(validate_server_url("http://localhost:8765"), "http://localhost:8765")
        for url in ("http://www.example.com", "file:///tmp/x", "https://user:pass@example.com", "https://example.com/foo"):
            with self.subTest(url=url), self.assertRaises(WorkerError):
                validate_server_url(url)

    def test_safe_workspace_and_write_optin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.txt").write_text("hello")
            (root / ".secret").write_text("private")
            self.assertIn("test.txt", execute("list_files", {"path": "."}, root))
            self.assertNotIn(".secret", execute("list_files", {"path": "."}, root))
            self.assertIn("Linux" if __import__('platform').system() == "Linux" else __import__('platform').system(),
                          execute("system_info", {}))
            with self.assertRaises(WorkerError):
                execute("write_note", {"text": "test"}, root, False)
            reply = execute("write_note", {"text": "note contents"}, root, True)
            self.assertIn("AgentTeamInbox", reply)
            self.assertEqual(len(list((root / "AgentTeamInbox").glob("note-*.txt"))), 1)
            with self.assertRaises(WorkerError):
                workspace_path(root, "../outside")
            with self.assertRaises(WorkerError):
                workspace_path(root, ".secret")
            with self.assertRaises(WorkerError):
                execute("shell", {"command": "calc"}, root, True)


if __name__ == "__main__":
    unittest.main()
