"""Opt-in Windows/Linux desktop worker that polls a trusted HTTPS hub.

No shell access or arbitrary code execution. Filesystem operations are confined
under the explicitly selected workspace folder, with hidden paths excluded.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit


class WorkerError(ValueError):
    pass


def validate_server_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise WorkerError("服务器地址只允许根地址，不能包含账号、路径和参数")
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return url
    raise WorkerError("远程服务器必须使用 HTTPS；HTTP 只允许本机测试")


def workspace_path(workspace: Path, rel: str = ".") -> Path:
    if not isinstance(rel, str) or not rel or len(rel) > 200 or Path(rel).is_absolute():
        raise WorkerError("目录路径无效")
    relative = Path(rel)
    if any(part.startswith(".") and part != "." for part in relative.parts) or ".." in relative.parts:
        raise WorkerError("不可访问隐藏目录或工作目录外的文件")
    root = workspace.resolve(strict=True)
    result = (root / relative).resolve(strict=True)
    if not result.is_relative_to(root):
        raise WorkerError("目标超出授权工作目录")
    return result


def execute(action: str, payload: dict, workspace: Path | None = None,
            allow_write: bool = False) -> str:
    if action == "system_info":
        return json.dumps({"platform": platform.system(), "version": platform.release(),
                           "python": platform.python_version()}, ensure_ascii=False)
    if action == "notify":
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise WorkerError("任务内容无效")
        # Remove terminal control characters from untrusted messages.
        readable = "".join(ch for ch in text[:8000] if ch.isprintable() or ch in "\n\t")
        print(f"\n[来自手机的任务通知]\n{readable}\n", flush=True)
        return "已送达电脑端任务接收器（未自动执行）"
    if workspace is None:
        raise WorkerError("此操作需要先通过 --workspace 授权工作目录")
    if action == "list_files":
        target = workspace_path(workspace, payload.get("path", "."))
        if not target.is_dir():
            raise WorkerError("路径不是文件夹")
        items = sorted((p for p in target.iterdir() if not p.name.startswith(".") and not p.is_symlink()),
                       key=lambda p: p.name.lower())[:80]
        return json.dumps([{"name": p.name, "type": "directory" if p.is_dir() else "file"}
                           for p in items], ensure_ascii=False)
    if action == "write_note":
        if not allow_write:
            raise WorkerError("电脑端未开启 --allow-write，拒绝写入")
        note = payload.get("text", "")
        if not isinstance(note, str) or not (1 <= len(note) <= 8000):
            raise WorkerError("便签内容无效")
        inbox = workspace.resolve(strict=True) / "AgentTeamInbox"
        if inbox.is_symlink():
            raise WorkerError("目标目录不能是符号链接")
        inbox.mkdir(exist_ok=True)
        from uuid import uuid4
        filename = "note-" + uuid4().hex[:12] + ".txt"
        destination = inbox / filename
        with destination.open("x", encoding="utf-8") as f:
            f.write(note)
        return f"已保存到授权工作目录的 AgentTeamInbox/{filename}"
    raise WorkerError("未知/未授权的任务类型，拒绝执行")


def request_json(server: str, token: str, method: str, path: str, data: dict | None = None) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = request.Request(server + path, data=body, method=method,
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read(100000).decode("utf-8"))
    except error.HTTPError as exc:
        raise WorkerError(f"服务器返回 HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise WorkerError("无法连接远程服务器") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Team Windows/Linux 远程任务接收器（非原生桌面 App）")
    parser.add_argument("--server", default=os.getenv("AGENT_TEAM_HUB_URL", ""))
    parser.add_argument("--workspace", type=Path, help="可列目录/保存便签的授权文件夹")
    parser.add_argument("--allow-write", action="store_true", help="允许在工作目录的 AgentTeamInbox 新建便签")
    parser.add_argument("--once", action="store_true", help="只检查一次，适合测试")
    parser.add_argument("--interval", type=int, default=4, help="轮询间隔（秒，至少 3）")
    args = parser.parse_args()
    if not args.server:
        parser.error("请通过 --server 或 AGENT_TEAM_HUB_URL 设置服务器地址")
    server = validate_server_url(args.server)
    token = os.getenv("AGENT_TEAM_DEVICE_TOKEN", "")
    if not token:
        parser.error("必须设置 AGENT_TEAM_DEVICE_TOKEN（一次性配对密钥）")
    if args.workspace and not args.workspace.is_dir():
        parser.error("--workspace 必须是现有文件夹")
    while True:
        try:
            request_json(server, token, "POST", "/api/device/heartbeat",
                         {"platform": platform.system()})
            data = request_json(server, token, "GET", "/api/device/jobs/next")
            job = data.get("task")
            if job:
                try:
                    result = execute(job["action"], job["payload"], args.workspace, args.allow_write)
                    ok = True
                except (WorkerError, OSError, ValueError) as exc:
                    result, ok = str(exc), False
                request_json(server, token, "POST", f"/api/device/jobs/{job['id']}/result",
                             {"ok": ok, "result": result})
                print(f"任务 {job['id'][:8]}：{'完成' if ok else '拒绝/失败'}", flush=True)
        except WorkerError as exc:
            print(f"连接失败：{exc}", file=sys.stderr, flush=True)
        if args.once:
            break
        time.sleep(max(args.interval, 3))


if __name__ == "__main__":
    main()
