"""Opt-in Windows/Linux desktop worker that polls a trusted HTTPS hub.

No shell access or arbitrary code execution. Filesystem operations are confined
under the explicitly selected workspace folder, with hidden paths excluded.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
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
    # Disallow symlinks/junctions at *any* component, even when the target stays
    # inside the workspace (e.g. alias -> hidden/secrets).
    cursor = root
    for component in relative.parts:
        if component == ".":
            continue
        cursor = cursor / component
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise WorkerError("工作目录内不允许通过软链接或联接点读取文件")
    try:
        result = (root / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise WorkerError("请求的文件或目录不存在") from exc
    if not result.is_relative_to(root) or any(part.startswith(".") for part in result.relative_to(root).parts):
        raise WorkerError("目标超出授权工作目录")
    return result


TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv", ".json", ".py", ".log", ".yaml", ".yml", ".toml"})


def search_text(workspace: Path, directory: str, query: str) -> str:
    if not isinstance(query, str) or not (1 <= len(query.strip()) <= 100):
        raise WorkerError("搜索关键词应为 1–100 个字符")
    root = workspace.resolve(strict=True)
    current = workspace_path(root, directory)
    if not current.is_dir():
        raise WorkerError("指定的搜索位置不是文件夹")
    results = []
    scanned = 0
    keyword = query.casefold()
    for directory_path, subdirs, filenames in os.walk(current, followlinks=False):
        # Do not traverse symlinks, Windows junctions, or hidden directories.
        subdirs[:] = sorted(name for name in subdirs if not name.startswith(".")
                            and name not in {"__pycache__", "node_modules"}
                            and not (Path(directory_path) / name).is_symlink()
                            and not (hasattr(Path(directory_path) / name, "is_junction")
                                     and (Path(directory_path) / name).is_junction()))
        for name in sorted(filenames):
            if scanned >= 200 or len(results) >= 30:
                break
            file = Path(directory_path) / name
            if name.startswith(".") or file.is_symlink() or (hasattr(file, "is_junction") and file.is_junction()):
                continue
            if file.suffix.lower() not in TEXT_EXTENSIONS or not file.is_file():
                continue
            try:
                if file.stat().st_size > 120000:
                    continue
                # Revalidate the path, after enumeration, before each read.
                safe = workspace_path(root, str(file.relative_to(root)))
                scanned += 1
                with safe.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if keyword in line.casefold():
                            results.append({"file": safe.relative_to(root).as_posix(),
                                            "line": line_number, "text": line.strip()[:220]})
                            if len(results) >= 30:
                                break
            except (OSError, UnicodeError, WorkerError):
                continue
        if scanned >= 200 or len(results) >= 30:
            break
    return json.dumps({"matches": results, "files_scanned": scanned,
                       "truncated": scanned >= 200 or len(results) >= 30}, ensure_ascii=False)


def run_project_tests(workspace: Path) -> str:
    root = workspace.resolve(strict=True)
    tests = workspace_path(root, "tests")
    if not tests.is_dir():
        raise WorkerError("授权目录内没有 tests 文件夹，拒绝执行")
    # Run only the pinned command, never an LLM-provided command or shell string.
    # Python tests themselves execute trusted workspace code; this is an opt-in.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("AGENT_TEAM_") and not any(
               marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "AUTH_"))}
    src = root / "src"
    if src.is_dir() and not src.is_symlink():
        env["PYTHONPATH"] = str(src)
    else:
        env.pop("PYTHONPATH", None)
    try:
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                              cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", timeout=90, check=False)
    except subprocess.TimeoutExpired as exc:
        raise WorkerError("自动测试超过 90 秒，已终止") from exc
    # Strip terminal escape/control sequences from output (not a secret scrubber).
    clean = "".join(c for c in proc.stdout[-12000:] if c.isprintable() or c in "\n\t")
    result = f"退出码：{proc.returncode}\n{clean}"
    if proc.returncode:
        raise WorkerError(result[:12000])
    return result[:12000]


def execute(action: str, payload: dict, workspace: Path | None = None,
            allow_write: bool = False, allow_tests: bool = False) -> str:
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
    if action == "read_text":
        target = workspace_path(workspace, payload.get("path", ""))
        if not target.is_file():
            raise WorkerError("目标不是普通文件")
        if target.suffix.lower() not in TEXT_EXTENSIONS:
            raise WorkerError("只允许读取常用的纯文本文件类型")
        if target.stat().st_size > 12000:
            raise WorkerError("文件超过 12KB，请缩小范围后再读取")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise WorkerError("文件不是有效的 UTF-8 文本") from exc
    if action == "search_text":
        return search_text(workspace, payload.get("path", "."), payload.get("query", ""))
    if action == "run_tests":
        if not allow_tests:
            raise WorkerError("电脑端尚未启用 --allow-tests；拒绝运行项目测试代码")
        return run_project_tests(workspace)
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
    parser.add_argument("--allow-tests", action="store_true",
                        help="允许运行工作目录中的 unittest 测试（测试代码具有本机用户权限，只用于可信项目）")
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
                    result = execute(job["action"], job["payload"], args.workspace, args.allow_write, args.allow_tests)
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
