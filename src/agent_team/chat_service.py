"""Provider adapters and bounded group collaboration engine."""
from __future__ import annotations

import json
import re
from urllib import error, request
from urllib.parse import quote, urlsplit

from .storage import Store


class ChatError(ValueError):
    pass


def validate_endpoint(endpoint: str, provider: str) -> str:
    if provider == "demo":
        return ""
    endpoint = endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ChatError("接口地址不能包含账号密码、查询参数或锚点")
    if parsed.scheme == "https" and parsed.hostname:
        return endpoint
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return endpoint
    raise ChatError("接口地址必须是 HTTPS，或本机 localhost HTTP（例如 Ollama）")


def _post_json(url: str, headers: dict[str, str], payload: dict, *, timeout: int = 60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except error.HTTPError as e:
        # Never leak API keys, proxy response bodies or provider internals into UI.
        raise ChatError(f"模型接口返回 HTTP {e.code}。请检查模型、Key、余额或权限。") from e
    except (error.URLError, TimeoutError) as e:
        raise ChatError("模型请求失败：网络错误或超时。") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise ChatError("模型接口返回的内容不是有效 JSON。") from e


def generate_reply(credential: dict, model: str, system: str, history: list[dict]) -> str:
    provider = credential["provider"]
    if provider == "demo":
        latest = next((m["content"] for m in reversed(history) if m["role"] == "user"), "你好")
        return f"【演示模式，不是真实 AI 回复】\n我收到消息：{latest[:180]}\n\n请在「模型凭据」中配置 API Key，并为智能体分配模型以开始真实聊天。"
    key = credential.get("api_key", "")
    if not key and provider != "ollama":
        raise ChatError("这个模型凭据缺少 API Key")
    base = validate_endpoint(credential["base_url"], provider)
    if provider == "anthropic":
        # Anthropic's Messages API separates system instructions from chat messages.
        user_messages = [{"role": m["role"], "content": m["content"]} for m in history]
        data = _post_json(base.rstrip("/") + "/messages",
                          {"x-api-key": key, "anthropic-version": "2023-06-01"},
                          {"model": model, "max_tokens": 2048, "system": system,
                           "messages": user_messages})
        try:
            answer = "\n".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise ChatError("Anthropic 返回了意外的数据结构") from e
    elif provider == "gemini":
        messages = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in history]
        url = base.rstrip("/") + "/models/" + quote(model, safe="") + ":generateContent"
        data = _post_json(url, {"x-goog-api-key": key},
                          {"systemInstruction": {"parts": [{"text": system}]}, "contents": messages})
        try:
            answer = "\n".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as e:
            raise ChatError("Gemini 未返回可显示的文本") from e
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com"
            headers["X-Title"] = "Agent Team"
        data = _post_json(base.rstrip("/") + "/chat/completions", headers,
                          {"model": model, "stream": False, "messages": [
                              {"role": "system", "content": system}, *history]})
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ChatError("模型未返回兼容 Chat Completions 的文本") from e
        if not isinstance(answer, str):
            raise ChatError("当前模型的返回内容不属于已支持的纯文本格式")
    if not answer or not answer.strip():
        raise ChatError("模型返回了空回复")
    return answer.strip()[:50000]


def pick_agents(conversation: dict, agent_list: list[dict], text: str) -> list[dict]:
    by_id = {a["id"]: a for a in agent_list}
    members = [by_id[aid] for aid in conversation["agent_ids"] if aid in by_id]
    if not members:
        raise ChatError("会话没有有效的智能体成员")
    if conversation["kind"] == "direct":
        return members[:1]
    # @name or @remark targets an individual. Longest name first for deterministic resolution.
    names = sorted([(n, a) for a in members for n in {a["name"], a["remark"]} if n],
                   key=lambda pair: len(pair[0]), reverse=True)
    found: list[dict] = []
    for name, agent in names:
        if re.search(r"(?<![\w])@" + re.escape(name) + r"(?=$|[\s，,。：:!?！?])", text, re.UNICODE):
            if agent not in found:
                found.append(agent)
    if found:
        return found[:4]
    if conversation["mode"] == "roundtable":
        return members[:4]
    if conversation["mode"] == "cooperate":
        return members[:4]
    # Mention mode defaults to the first participant rather than spending across all models.
    return members[:1]


def assemble_history(messages: list[dict]) -> list[dict]:
    history: list[dict] = []
    for item in messages[-24:]:
        if item["sender"] == "user":
            history.append({"role": "user", "content": item["content"][:6000]})
        elif item["sender"] == "agent":
            history.append({"role": "assistant", "content":
                            f"[{item.get('agent_name') or '智能体'}]: {item['content'][:6000]}"})
    # Merge consecutive turns from the same role. Anthropic/Gemini have stricter
    # conversation-shape requirements than basic Chat Completions providers.
    merged: list[dict] = []
    for message in history:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] += "\n\n" + message["content"]
        else:
            merged.append(dict(message))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def reply_to_message(store: Store, conversation_id: str, text: str) -> dict:
    convo = store.conversation(conversation_id)
    if not convo:
        raise ChatError("会话不存在")
    text = text.strip()
    if not text or len(text) > 8000:
        raise ChatError("消息不能为空或超过 8000 字")
    selected = pick_agents(convo, store.agents(), text)
    outgoing = [store.append_message(conversation_id, "user", text)]
    errors = []
    all_skills = {skill["id"]: skill for skill in store.skills()}
    for index, agent in enumerate(selected):
        credential = store.credential(agent["credential_id"] or "")
        if not credential:
            errors.append(f"{agent['name']}：未配置有效模型凭据")
            continue
        skill_text = "\n".join(f"- {all_skills[k]['name']}：{all_skills[k]['instructions']}"
                               for k in agent["skill_ids"] if k in all_skills)
        system = (f"你是群聊中的一名独立智能体，名字为 {agent['name']}，备注为 {agent['remark']}。\n"
                  f"你的人格和职责：{agent['system_prompt'] or '自然、准确地回答用户。'}\n"
                  "自然地与用户和其他成员协作，不要假装是别的智能体。"
                  "不要声称你拥有未配置的工具，也不要伪称执行了外部操作。\n"
                  + (f"已授权的文本型 Skill 指令：\n{skill_text}\n" if skill_text else ""))
        if convo["mode"] == "cooperate" and len(selected) > 1:
            system += ("这是一次有界协作，第 %d/%d 位发言。参考上文其他智能体的结论，"
                       "补充遗漏、质疑不可靠信息或给出可执行建议；不要机械重复。\n" % (index + 1, len(selected)))
        history = assemble_history(store.messages(conversation_id, limit=30))
        try:
            answer = generate_reply(credential, agent["model"], system, history)
            outgoing.append(store.append_message(conversation_id, "agent", answer,
                                          agent_id=agent["id"], agent_name=agent["name"]))
        except ChatError as exc:
            errors.append(f"{agent['name']}：{exc}")
    return {"messages": outgoing, "errors": errors, "replies": len(outgoing) - 1}
