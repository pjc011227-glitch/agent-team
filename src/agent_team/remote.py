"""Minimal owner-controlled device relay for remote desktop jobs.

The hub stores tasks; workers poll outbound from Windows/Linux machines.  There is
no remote shell and devices never receive hub model API keys or owner tokens.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .storage import Store, uid, utcnow

ALLOWED_ACTIONS = frozenset({"system_info", "list_files", "read_text", "notify", "write_note", "search_text", "run_tests"})


class RemoteError(ValueError):
    pass


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def initialize(store: Store) -> None:
    with store.connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS remote_devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT, revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS remote_tasks (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES remote_devices(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                retry_of TEXT
            );
            CREATE INDEX IF NOT EXISTS remote_tasks_device_status_idx
                ON remote_tasks (device_id, status, created_at);
        """)
        # Compatible with already-created v0.3 SQLite databases.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(remote_tasks)")}
        if "retry_of" not in columns:
            db.execute("ALTER TABLE remote_tasks ADD COLUMN retry_of TEXT")


def register(store: Store, name: str) -> dict[str, Any]:
    # The secret is returned *once* to the owner, never in list endpoints.
    token = secrets.token_urlsafe(32)
    device_id = uid()
    with store.connection() as db:
        db.execute("""INSERT INTO remote_devices
            (id, name, token_hash, created_at) VALUES (?, ?, ?, ?)""",
            (device_id, name, _digest(token), utcnow()))
    return {"id": device_id, "name": name, "device_token": token}


def devices(store: Store) -> list[dict[str, Any]]:
    return store.query("""SELECT id, name, platform, last_seen_at, created_at
        FROM remote_devices WHERE revoked=0 ORDER BY created_at DESC""")


def device_from_token(store: Store, token: str) -> dict[str, Any] | None:
    if not token or len(token) > 256:
        return None
    rows = store.query("""SELECT id, name, platform FROM remote_devices
        WHERE token_hash=? AND revoked=0""", (_digest(token),))
    return rows[0] if rows else None


def heartbeat(store: Store, token: str, platform: str) -> dict[str, Any] | None:
    device = device_from_token(store, token)
    if device is None:
        return None
    with store.connection() as db:
        db.execute("UPDATE remote_devices SET last_seen_at=?, platform=? WHERE id=?",
                   (utcnow(), platform[:100], device["id"]))
    return {"id": device["id"], "name": device["name"]}


def revoke(store: Store, device_id: str) -> bool:
    with store.connection() as db:
        changed = db.execute("UPDATE remote_devices SET revoked=1 WHERE id=? AND revoked=0",
                             (device_id,)).rowcount
        if changed:
            db.execute("""UPDATE remote_tasks SET status='cancelled', finished_at=?
                WHERE device_id=? AND status='queued'""", (utcnow(), device_id))
            db.execute("""UPDATE remote_tasks SET status='interrupted', finished_at=?,
                result='设备配对已撤销，执行结果不再接收' WHERE device_id=? AND status='running'""",
                (utcnow(), device_id))
    return bool(changed)


def enqueue(store: Store, device_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise RemoteError("当前动作未被授权；不支持任意 Shell/脚本执行")
    if len(json.dumps(payload, ensure_ascii=False)) > 10000:
        raise RemoteError("任务参数过长")
    if action in {"notify", "write_note"}:
        text = payload.get("text", "")
        if not isinstance(text, str) or not (1 <= len(text) <= 8000):
            raise RemoteError("请输入不超过 8000 字的任务内容")
    if action in {"list_files", "read_text", "search_text"} and not isinstance(payload.get("path", "."), str):
        raise RemoteError("目录路径无效")
    if action == "search_text":
        query = payload.get("query", "")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
            raise RemoteError("搜索关键词应为 1–100 个字符")
    if action == "run_tests" and payload:
        raise RemoteError("测试任务不允许自定义命令或参数")
    task_id = uid()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        device = db.execute("SELECT id FROM remote_devices WHERE id=? AND revoked=0",
                            (device_id,)).fetchone()
        if device is None:
            raise RemoteError("设备不存在或已撤销")
        queued = db.execute("""SELECT COUNT(*) FROM remote_tasks
            WHERE device_id=? AND status IN ('queued','running')""", (device_id,)).fetchone()[0]
        if queued >= 30:
            raise RemoteError("该设备的待处理任务过多")
        db.execute("""INSERT INTO remote_tasks
            (id, device_id, action, payload_json, created_at) VALUES (?,?,?,?,?)""",
            (task_id, device_id, action, json.dumps(payload, ensure_ascii=False), utcnow()))
    return {"id": task_id, "device_id": device_id, "action": action, "status": "queued"}


def mark_interrupted(store: Store, timeout_seconds: int = 180) -> int:
    """Never automatically replay an action: a crashed worker may have executed it.

    Expired claims are marked interrupted so the owner can explicitly retry.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    with store.connection() as db:
        return db.execute("""UPDATE remote_tasks SET status='interrupted', finished_at=?,
            result='电脑在处理期间断开或执行超时；请核对实际结果后手动重试'
            WHERE status='running' AND started_at < ?""", (utcnow(), cutoff)).rowcount


def tasks(store: Store, device_id: str) -> list[dict[str, Any]]:
    mark_interrupted(store)
    return store.query("""SELECT id, device_id, action, status, result, created_at,
        started_at, finished_at, retry_of FROM remote_tasks WHERE device_id=?
        ORDER BY created_at DESC, rowid DESC LIMIT 50""", (device_id,))


def cancel_task(store: Store, device_id: str, task_id: str) -> None:
    """Cancel only queued tasks; in-progress work cannot be stopped remotely yet."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM remote_tasks WHERE id=? AND device_id=?",
                         (task_id, device_id)).fetchone()
        if row is None:
            raise RemoteError("任务不存在")
        if row["status"] != "queued":
            raise RemoteError("只能取消排队中的任务；正在执行的操作可能已经生效")
        db.execute("""UPDATE remote_tasks SET status='cancelled', finished_at=?
            WHERE id=? AND device_id=? AND status='queued'""", (utcnow(), task_id, device_id))


def retry_task(store: Store, device_id: str, task_id: str) -> dict[str, Any]:
    mark_interrupted(store)
    with store.connection() as db:
        row = db.execute("""SELECT action, payload_json, status FROM remote_tasks
            WHERE id=? AND device_id=?""", (task_id, device_id)).fetchone()
        if row is None:
            raise RemoteError("任务不存在")
        if row["status"] not in ("failed", "cancelled", "interrupted"):
            raise RemoteError("只能重试失败、取消或中断的任务")
        # Inherit server-side validated payload, but always generate a NEW task ID.
        action, payload = row["action"], json.loads(row["payload_json"])
    result = enqueue(store, device_id, action, payload)
    with store.connection() as db:
        db.execute("UPDATE remote_tasks SET retry_of=? WHERE id=?", (task_id, result["id"]))
    return result


def claim(store: Store, token: str) -> dict[str, Any] | None:
    device = device_from_token(store, token)
    if device is None:
        raise RemoteError("设备凭据无效")
    mark_interrupted(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT id, action, payload_json FROM remote_tasks
            WHERE device_id=? AND status='queued' ORDER BY rowid LIMIT 1""",
            (device["id"],)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE remote_tasks SET status='running', started_at=? WHERE id=?",
                   (utcnow(), row["id"]))
        return {"id": row["id"], "action": row["action"],
                "payload": json.loads(row["payload_json"])}


def finish(store: Store, token: str, task_id: str, ok: bool, result: str) -> bool:
    device = device_from_token(store, token)
    if device is None:
        raise RemoteError("设备凭据无效")
    with store.connection() as db:
        changed = db.execute("""UPDATE remote_tasks
            SET status=?, result=?, finished_at=?
            WHERE id=? AND device_id=? AND status='running'""",
            ("completed" if ok else "failed", result[:16000], utcnow(),
             task_id, device["id"])).rowcount
    return bool(changed)
