"""CharacterEngine 接口（ADR-0001 适配层）。

编排核心只依赖本协议；DshEngine（真实）/ FakeEngine（测试）实现同一接口。
换引擎/混用引擎的成本被限制在 engines/ 内。
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from mrp.shared.models import Character, EngineHealth, EngineReply, TurnContext

# R33：生成过程中的增量文本回调（真流式时在工作线程触发，实现须线程安全）。
# 当前 dsh SDK 无增量通知（spike FAIL，见 document/spike/v3/m8-dsh-streaming.md），
# 编排层走伪流式；此参数为未来真流式预留的协议契约。
DeltaCallback = Callable[[str], None]


@runtime_checkable
class CharacterEngine(Protocol):
    """单个角色的引擎抽象。每个角色一个实例（ADR-0001：每角色一进程）。"""

    async def start(self, character: Character) -> None:
        """初始化引擎（DshEngine：生成 profile 并拉起子进程）。幂等。"""
        ...

    async def stop(self) -> None:
        """释放资源（杀子进程、保留 dsh_home）。幂等。"""
        ...

    async def is_alive(self) -> bool:
        ...

    async def generate(self, ctx: TurnContext, *, on_delta: DeltaCallback | None = None) -> EngineReply:
        """执行一次角色回合。

        ctx.visible_messages 已过可见性裁决；injections 已排序；
        引擎内部用 shared.prompt.PromptComposer 组装最终 prompt（每轮重组，
        计划 §4：swipe/离席/事后注入都需要编排核心掌控历史）。

        on_delta（R33，additive）：引擎支持真流式时逐片回调增量文本；
        不支持时静默忽略（DshEngine 现状）。返回值仍是完整回复。
        """
        ...

    def health(self) -> EngineHealth:
        ...
