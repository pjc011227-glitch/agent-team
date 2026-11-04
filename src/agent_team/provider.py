"""模型服务接口：内置离线演示与 Chat Completions HTTP 适配器。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """模型服务调用失败。"""


class DemoProvider:
    """不调用模型，演示角色交接。并非真正 AI 推理。"""

    is_demo = True

    def generate(self, system: str, user: str) -> str:
        if "[ROLE:PLANNER]" in system:
            return "1. 明确目标与约束。\n2. 拆分子任务。\n3. 由专家执行。\n4. 审核并交付。"
        if "[ROLE:REVIEWER]" in system:
            return "PASS（演示模式：未做真实质量验证）"
        return "[演示结果] 已展示专家执行步骤。此模式不调用大模型，因此不会生成真实任务解决方案。"


@dataclass(frozen=True)
class HTTPChatProvider:
    api_key: str
    model: str
    base_url: str
    timeout: float = 45.0
    is_demo: bool = False

    def __post_init__(self) -> None:
        if not self.api_key or not self.model:
            raise ValueError("真实模式需配置 API Key 和模型名称")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("BASE_URL 仅接受 HTTPS 或本机 HTTP 地址")

    def generate(self, system: str, user: str) -> str:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as res:
                data = json.load(res)
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError("模型返回了空文本")
            return content.strip()
        except (error.HTTPError, error.URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
            if isinstance(exc, error.HTTPError):
                detail = f"HTTP {exc.code}"
            else:
                detail = type(exc).__name__
            raise ProviderError(f"模型调用失败（{detail}）；请检查模型、网络及接口配置") from exc


def provider_from_env() -> DemoProvider | HTTPChatProvider:
    """默认离线；只有同时配置密钥和模型才会启动真实 API 模式。"""
    key = os.getenv("AGENT_TEAM_API_KEY", "").strip()
    model = os.getenv("AGENT_TEAM_MODEL", "").strip()
    if bool(key) != bool(model):
        raise ValueError("AGENT_TEAM_API_KEY 与 AGENT_TEAM_MODEL 必须同时设置")
    if not key:
        return DemoProvider()
    return HTTPChatProvider(
        api_key=key,
        model=model,
        base_url=os.getenv("AGENT_TEAM_BASE_URL", "https://api.openai.com/v1").strip(),
    )
