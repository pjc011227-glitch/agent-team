"""Local SQLite storage. API keys are encrypted using a private Fernet key file.

Not intended as a multi-tenant internet-facing database. Remote deployment must add
HTTPS and access controls; the development server binds to loopback by default.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cryptography.fernet import Fernet


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid() -> str:
    return str(uuid.uuid4())


DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://127.0.0.1:11434/v1",
    "compatible": "https://api.example.com/v1",
    "demo": "",
}


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("AGENT_TEAM_DATA_DIR") or Path.home() / ".agent-team")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.db_path = self.root / "agent-team.sqlite3"
        key_path = self.root / "encryption.key"
        if not key_path.exists():
            # O_EXCL prevents a second process replacing the same master key.
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.cipher = Fernet(key_path.read_bytes())
        self.init_db()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL,
                    base_url TEXT NOT NULL, encrypted_key TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    instructions TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, remark TEXT NOT NULL DEFAULT '',
                    avatar TEXT NOT NULL DEFAULT '🤖', system_prompt TEXT NOT NULL DEFAULT '',
                    credential_id TEXT REFERENCES credentials(id) ON DELETE SET NULL,
                    model TEXT NOT NULL DEFAULT 'demo', skills_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    mode TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_agents (
                    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
                    agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(conversation_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    sender TEXT NOT NULL, agent_id TEXT, agent_name TEXT,
                    content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation_idx
                    ON messages(conversation_id, created_at);
            """)
            db.execute("INSERT OR IGNORE INTO credentials VALUES (?,?,?,?,?,?)", (
                "demo", "演示模式（不调用 LLM）", "demo", "", "", utcnow()))
            # Make the first launch usable without any credential/account.
            if db.execute("SELECT count(*) FROM agents").fetchone()[0] == 0:
                for name, remark, emoji, prompt in (
                    ("小策", "规划助手", "🧭", "擅长规划和拆分任务，表达清晰，简洁直接。"),
                    ("小研", "研究助手", "🔬", "善于分析证据和找出假设，不编造来源。"),
                    ("小码", "开发助手", "💻", "帮助设计、实现和检查代码；不声称执行了未执行的操作。"),
                ):
                    db.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?)", (
                        uid(), name, remark, emoji, prompt, "demo", "demo", "[]", utcnow()))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]

    def credentials(self, reveal: bool = False) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM credentials ORDER BY created_at")
        for row in rows:
            if reveal:
                row["api_key"] = (self.cipher.decrypt(row["encrypted_key"].encode()).decode()
                                  if row["encrypted_key"] else "")
            row["has_key"] = bool(row["encrypted_key"])
            del row["encrypted_key"]
        return rows

    def credential(self, credential_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        encrypted = data.pop("encrypted_key")
        data["api_key"] = self.cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
        return data

    def create_credential(self, name: str, provider: str, base_url: str, key: str) -> dict[str, Any]:
        record_id = uid()
        encrypted = self.cipher.encrypt(key.encode()).decode() if key else ""
        with self.connection() as db:
            db.execute("INSERT INTO credentials VALUES (?,?,?,?,?,?)",
                       (record_id, name, provider, base_url, encrypted, utcnow()))
        return next(c for c in self.credentials() if c["id"] == record_id)

    def agents(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM agents ORDER BY created_at, name")
        for row in rows:
            row["skill_ids"] = json.loads(row.pop("skills_json"))
        return rows

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        return next((a for a in self.agents() if a["id"] == agent_id), None)

    def create_agent(self, *, name: str, remark: str, avatar: str, system_prompt: str,
                     credential_id: str, model: str, skill_ids: list[str]) -> dict[str, Any]:
        record_id = uid()
        with self.connection() as db:
            db.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?)", (
                record_id, name, remark, avatar, system_prompt, credential_id, model,
                json.dumps(skill_ids), utcnow()))
        return self.agent(record_id) or {}

    def update_agent(self, agent_id: str, *, name: str, remark: str, avatar: str,
                     system_prompt: str, credential_id: str, model: str,
                     skill_ids: list[str]) -> dict[str, Any] | None:
        with self.connection() as db:
            cursor = db.execute("""UPDATE agents SET name=?, remark=?, avatar=?, system_prompt=?,
                credential_id=?, model=?, skills_json=? WHERE id=?""", (
                name, remark, avatar, system_prompt, credential_id, model,
                json.dumps(skill_ids), agent_id))
        return self.agent(agent_id) if cursor.rowcount else None

    def skills(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM skills ORDER BY created_at DESC")

    def create_skill(self, name: str, description: str, instructions: str) -> dict[str, Any]:
        record_id = uid()
        with self.connection() as db:
            db.execute("INSERT INTO skills VALUES (?,?,?,?,?)",
                       (record_id, name, description, instructions, utcnow()))
        return next(s for s in self.skills() if s["id"] == record_id)

    def conversations(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM conversations ORDER BY created_at DESC")
        for row in rows:
            row["agent_ids"] = [x["agent_id"] for x in self.query(
                "SELECT agent_id FROM conversation_agents WHERE conversation_id=? ORDER BY position",
                (row["id"],))]
            latest = self.query("SELECT content FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1", (row["id"],))
            row["preview"] = latest[0]["content"][:120] if latest else ""
        return rows

    def conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return next((c for c in self.conversations() if c["id"] == conversation_id), None)

    def create_conversation(self, name: str, kind: str, mode: str, agent_ids: list[str]) -> dict[str, Any]:
        record_id = uid()
        with self.connection() as db:
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?)",
                       (record_id, name, kind, mode, utcnow()))
            db.executemany("INSERT INTO conversation_agents VALUES (?,?,?)",
                           [(record_id, aid, i) for i, aid in enumerate(agent_ids)])
        return self.conversation(record_id) or {}

    def messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        newest_first = self.query(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT ?",
            (conversation_id, limit))
        return list(reversed(newest_first))

    def append_message(self, conversation_id: str, sender: str, content: str,
                       agent_id: str | None = None, agent_name: str | None = None) -> dict[str, Any]:
        record_id = uid()
        with self.connection() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (
                record_id, conversation_id, sender, agent_id, agent_name, content, utcnow()))
        return dict(self.query("SELECT * FROM messages WHERE id=?", (record_id,))[0])
