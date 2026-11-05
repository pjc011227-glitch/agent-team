"""Conservative Chinese/English shortcuts to *preview* safe desktop actions.

This module intentionally does not call an LLM and never queues a job. Unclear
requests must not be guessed, especially when they mention destructive actions.
"""
from __future__ import annotations

import re


class IntentError(ValueError):
    pass


_EXAMPLES = [
    "查看电脑状态", "列出工作目录", "列出目录 src", "读取 README.md",
    "搜索关键词 TODO", "运行单元测试", "保存便签：记得检查代码", "通知电脑：开始工作",
]
_PREFIX = re.compile(r"^(?:(?:请|帮我|麻烦|让电脑|在电脑上|电脑上|给我)\s*)+", re.IGNORECASE)
_FORBIDDEN = re.compile(r"删除|卸载|格式化|清空|执行(?:命令|脚本)|运行(?:命令|脚本)|终止进程|关闭电脑|关机|重启电脑", re.I)


def interpret(text: str) -> dict:
    """Return one explicit bounded action or raise; never execute from a sentence."""
    raw = text.strip()
    if not raw or len(raw) > 500:
        raise IntentError("请输入不超过 500 字的工作指令")
    cleaned = _PREFIX.sub("", raw).strip().rstrip("。.!！")
    if _FORBIDDEN.search(cleaned):
        raise IntentError("该命令涉及尚未授权的高风险操作；本版本不会安排执行")

    def preview(action: str, payload: dict, summary: str, detail: str = "") -> dict:
        return {"action": action, "payload": payload, "summary": summary,
                "detail": detail, "requires_confirmation": True}

    if re.fullmatch(r"(?:查看|查询|获取|显示)?\s*(?:电脑|系统|设备)(?:的)?(?:状态|信息|配置)|(?:system info|system status)", cleaned, re.I):
        return preview("system_info", {}, "查看电脑系统信息", "不会运行外部程序")
    if re.fullmatch(r"(?:(?:运行|执行|跑(?:一下)?|检查)(?:项目|代码|单元|自动化)?测试|(?:run|execute) tests?)", cleaned, re.I):
        return preview("run_tests", {}, "运行工作目录中的单元测试",
                       "需要电脑端显式启用 --allow-tests；会执行工作目录中的 Python 测试代码")

    match = re.fullmatch(r"(?:列出|查看|显示)(?:一下)?(?:工作目录|文件夹|目录|文件)(?:列表|内容)?(?:\s*[：:]?\s*(.*))?", cleaned, re.I)
    if match:
        path = (match.group(1) or ".").strip()
        if not path:
            path = "."
        return preview("list_files", {"path": path}, f"列出授权目录：{path}")
    match = re.fullmatch(r"(?:读取|打开|查看)(?:文本|文件)?\s*[：:]?\s+(.+)", cleaned, re.I)
    if match:
        path = match.group(1).strip()
        return preview("read_text", {"path": path}, f"读取文本：{path}", "限 12KB UTF-8 文本")
    match = re.fullmatch(r"(?:搜索|查找)(?:关键词|文本|代码)?\s*[：:]?\s*(.+)", cleaned, re.I)
    if match:
        keyword = match.group(1).strip()
        if not keyword or len(keyword) > 100:
            raise IntentError("搜索关键词应为 1–100 个字符")
        return preview("search_text", {"query": keyword, "path": "."},
                       f"在授权目录搜索：{keyword}", "只搜索小型纯文本文件，返回最多 30 个匹配")
    match = re.fullmatch(r"(?:保存|写入|记录)(?:一条)?便签\s*[：:]\s*(.+)", cleaned, re.I | re.S)
    if match:
        content = match.group(1).strip()
        return preview("write_note", {"text": content}, "保存一条工作便签",
                       "需要电脑端显式启用 --allow-write")
    match = re.fullmatch(r"(?:通知|告诉)(?:一下)?电脑\s*[：:]\s*(.+)", cleaned, re.I | re.S)
    if match:
        content = match.group(1).strip()
        return preview("notify", {"text": content}, "发送文字通知", "只显示通知，不会把通知当作程序运行")
    raise IntentError("暂时无法可靠理解这条指令；请使用支持的指令模板：" + " / ".join(_EXAMPLES))
