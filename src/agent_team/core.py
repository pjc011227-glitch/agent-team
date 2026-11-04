"""多智能体编排逻辑。MVP 不暴露外部执行工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ModelProvider(Protocol):
    is_demo: bool

    def generate(self, system: str, user: str) -> str: ...


SKILLS = {
    "researcher": "研究员：整理证据、区分事实与假设、指出信息缺口，不要捏造来源。",
    "coder": "程序员：提出合理架构、可测试实现和风险边界；没有工具时不要声称已经改过文件。",
    "writer": "写作者：围绕目标生成结构清晰、准确、可修改的文本。",
}


def choose_specialist(task: str) -> str:
    """示例关键词路由；以后可替换为分类模型或可配置路由器。"""
    query = task.lower()
    coding = ("代码", "编程", "脚本", "python", "bug", "api", "部署", "项目", "开发", "架构", "agent", "智能体")
    research = ("论文", "科研", "文献", "研究", "调研", "资料", "分析", "实验", "对比")
    if any(term in query for term in coding):
        return "coder"
    if any(term in query for term in research):
        return "researcher"
    return "writer"


@dataclass(frozen=True)
class RunResult:
    specialist: str
    plan: str
    draft: str
    review: str
    final: str
    revised: bool
    demo: bool


@dataclass
class AgentTeam:
    provider: ModelProvider

    def run(self, task: str) -> RunResult:
        if not task or not task.strip():
            raise ValueError("任务不能为空")
        task = task.strip()
        specialist = choose_specialist(task)
        plan = self.provider.generate(
            "[ROLE:PLANNER] 你是规划智能体。按目标、步骤、验证点写出简明方案；不虚构完成结果。",
            f"用户任务：\n{task}",
        )
        specialist_prompt = f"[ROLE:SPECIALIST] 你是{SKILLS[specialist]} 只给出当前权限下能完成的内容，明确局限。"
        draft = self.provider.generate(
            specialist_prompt,
            f"用户任务：\n{task}\n\n规划：\n{plan}\n\n请完成任务，给出实际可用结果。",
        )
        review = self.provider.generate(
            "[ROLE:REVIEWER] 你是审查员。检查答复是否满足用户任务、是否存在事实或安全问题。"
            "若合格，只输出 PASS；否则以 REVISE: 开头给出具体修改意见。",
            f"用户任务：\n{task}\n\n草稿：\n{draft}",
        )
        revised = not review.lstrip().upper().startswith("PASS")
        final = draft
        if revised:
            final = self.provider.generate(
                specialist_prompt,
                f"原始任务：\n{task}\n\n初稿：\n{draft}\n\n审查反馈：\n{review}\n\n"
                "请依据反馈修改为最终结果；如不能修复，明确告知限制。",
            )
        return RunResult(
            specialist=specialist,
            plan=plan,
            draft=draft,
            review=review,
            final=final,
            revised=revised,
            demo=self.provider.is_demo,
        )
