"""运行方式：python -m agent_team '你的任务'。"""

from __future__ import annotations

import argparse
import sys

from .core import AgentTeam
from .provider import ProviderError, provider_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Team 多智能体团队原型")
    parser.add_argument("task", help="需要团队处理的任务")
    parser.add_argument("--details", action="store_true", help="显示规划、草稿和审查反馈")
    args = parser.parse_args()
    try:
        result = AgentTeam(provider_from_env()).run(args.task)
    except (ValueError, ProviderError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"模式：{'离线演示（不调用大模型）' if result.demo else '真实模型 API'}")
    print(f"执行专家：{result.specialist}")
    if args.details:
        print(f"\n[规划]\n{result.plan}")
        print(f"\n[初稿]\n{result.draft}")
        print(f"\n[审查]\n{result.review}")
    print(f"\n[最终结果]\n{result.final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
