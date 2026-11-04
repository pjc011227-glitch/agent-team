"""Single-user, local-first chat gateway and web/PWA server."""
from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat_service import ChatError, reply_to_message, validate_endpoint
from .storage import DEFAULT_ENDPOINTS, Store


class CredentialInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=1500)


class AgentInput(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    remark: str = Field(default="", max_length=100)
    avatar: str = Field(default="🤖", max_length=8)
    system_prompt: str = Field(default="", max_length=10000)
    credential_id: str = Field(default="demo")
    model: str = Field(default="demo", min_length=1, max_length=160)
    skill_ids: list[str] = Field(default_factory=list, max_length=20)


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    instructions: str = Field(min_length=1, max_length=12000)


class ConversationInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["direct", "group"] = "direct"
    mode: Literal["mention", "roundtable", "cooperate"] = "mention"
    agent_ids: list[str] = Field(min_length=1, max_length=20)


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Agent Team", version="0.2.0", docs_url=None, redoc_url=None)
    app.state.store = Store(data_dir)
    access_token = os.getenv("AGENT_TEAM_ACCESS_TOKEN", "")

    def auth(x_agent_team_token: str | None = Header(default=None)):
        if access_token and not hmac.compare_digest(x_agent_team_token or "", access_token):
            raise HTTPException(status_code=401, detail="需要访问口令")

    def fail(error: str, status_code: int = 400):
        raise HTTPException(status_code=status_code, detail=error)

    @app.middleware("http")
    async def security_headers(req, call_next):
        response = await call_next(req)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'"
        )
        response.headers["Cache-Control"] = "no-store" if req.url.path.startswith("/api/") else "no-cache"
        return response

    @app.get("/api/status")
    def status():
        return {"ok": True, "protected": bool(access_token), "version": "0.2.0"}

    @app.get("/api/state")
    def state(x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        db: Store = app.state.store
        return {"credentials": db.credentials(), "agents": db.agents(),
                "skills": db.skills(), "conversations": db.conversations(),
                "providers": DEFAULT_ENDPOINTS}

    @app.post("/api/credentials", status_code=201)
    def create_credential(payload: CredentialInput, x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        if payload.provider not in DEFAULT_ENDPOINTS or payload.provider == "demo":
            fail("未知模型服务商")
        if not payload.api_key.strip() and payload.provider != "ollama":
            fail("此服务商需要 API Key")
        try:
            endpoint = validate_endpoint(payload.base_url or DEFAULT_ENDPOINTS[payload.provider], payload.provider)
        except ChatError as exc:
            fail(str(exc))
        return app.state.store.create_credential(payload.name.strip(), payload.provider, endpoint, payload.api_key.strip())

    def check_agent(payload: AgentInput):
        db: Store = app.state.store
        if not db.credential(payload.credential_id):
            fail("模型凭据不存在")
        valid_skills = {s["id"] for s in db.skills()}
        if any(sid not in valid_skills for sid in payload.skill_ids):
            fail("存在无效 Skill")
        if len(set(payload.skill_ids)) != len(payload.skill_ids):
            fail("Skill 不能重复")

    @app.post("/api/agents", status_code=201)
    def create_agent(payload: AgentInput, x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        check_agent(payload)
        return app.state.store.create_agent(**payload.model_dump())

    @app.put("/api/agents/{agent_id}")
    def update_agent(agent_id: str, payload: AgentInput,
                     x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        check_agent(payload)
        agent = app.state.store.update_agent(agent_id, **payload.model_dump())
        if not agent:
            fail("智能体不存在", 404)
        return agent

    @app.post("/api/skills", status_code=201)
    def create_skill(payload: SkillInput, x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        return app.state.store.create_skill(**payload.model_dump())

    @app.post("/api/conversations", status_code=201)
    def create_conversation(payload: ConversationInput,
                            x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        db: Store = app.state.store
        if len(set(payload.agent_ids)) != len(payload.agent_ids):
            fail("群成员不能重复")
        if payload.kind == "direct" and len(payload.agent_ids) != 1:
            fail("私聊必须恰好包含一个智能体")
        ids = {a["id"] for a in db.agents()}
        if any(aid not in ids for aid in payload.agent_ids):
            fail("存在无效智能体")
        return db.create_conversation(payload.name.strip(), payload.kind,
                                      payload.mode, payload.agent_ids)

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str, x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        db: Store = app.state.store
        if not db.conversation(conversation_id):
            fail("会话不存在", 404)
        return {"messages": db.messages(conversation_id, limit=200)}

    @app.post("/api/conversations/{conversation_id}/messages")
    def send_message(conversation_id: str, payload: MessageInput,
                     x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        try:
            return reply_to_message(app.state.store, conversation_id, payload.text)
        except ChatError as exc:
            fail(str(exc), 404 if "不存在" in str(exc) else 400)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, x_agent_team_token: str | None = Header(default=None)):
        auth(x_agent_team_token)
        with app.state.store.connection() as db:
            cursor = db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        if not cursor.rowcount:
            fail("会话不存在", 404)
        return {"ok": True}

    assets = Path(__file__).parent / "web"
    app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Team 的跨端聊天服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("AGENT_TEAM_ACCESS_TOKEN"):
        parser.error("监听局域网/公网地址前，必须先设置 AGENT_TEAM_ACCESS_TOKEN；还应使用 HTTPS/VPN")
    import uvicorn
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
