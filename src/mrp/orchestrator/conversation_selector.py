"""Choose one actual next speaker, or yield; never prewrite anyone's actions."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field

from mrp.llm import LlmConfig, chat_text_with_usage, extract_json
from mrp.shared.models import TokenUsage
from mrp.settings import provider_profile_id


@dataclass
class Selection:
    action: str
    speaker_id: str | None = None
    reason: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    reply_to_message_ids: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)


class ConversationSelector:
    def __init__(self, container):
        self.container = container

    async def select(self, *, state, run, candidates, public_messages) -> Selection:
        settings = self.container.settings
        cfg = LlmConfig(model=settings.auxiliary_model or settings.model,
            base_url=settings.gateway, api_key_env=self.container.config.api_key_env,
            api_key=settings.api_key, max_tokens=600,
            provider=settings.auxiliary_provider if provider_profile_id(settings.gateway) == "openrouter" else "",
            provider_allow_fallbacks=settings.provider_allow_fallbacks)
        if provider_profile_id(settings.gateway) == "openrouter":
            cfg.sampling = {"reasoning": {"enabled": False} if settings.thinking == "off" else {"effort": "low"}}
        scene = next((s for s in state.scenes if s.id == state.active_scene_id), None)
        from mrp.shared.actor_labels import build_actor_labels
        labels = build_actor_labels(state, public_messages, actor_ids=list(candidates))
        rows = []
        for cid, actor in candidates.items():
            card = getattr(actor, "card", None)
            hook = (getattr(card, "personality", "") or getattr(card, "description", "")) if card else actor.public_brief
            rows.append(f"{cid} | {labels.get(cid, cid)} | {hook[:250]}")
        prompt = [{"role": "system", "content": (
            "你只调度当前场景的自然交流，不写剧情正文。每次只选下一位发言者，结合刚发生的最新实际发言，"
            "不要一直回答旧玩家输入。允许A→B→A，人物可以沉默，不要求每人发言。"
            "候选是唯一允许发言的对象，不能代玩家或旁人决定、说话、写内心。"
            "不能预写未来、跳时间、换场景、强迫关系改变。幕后要求是作者要求，人物没有听见这条要求。"
            "没有自然接话理由时stop；需要当前玩家决定或答话时needs_user。"
            "可以接此前公开问题；reply_to_message_ids可选择一条或多条共享记录，不能引用未列出的消息。"
            "只输出JSON {\"action\":\"speak|stop|needs_user\",\"speaker_id\":\"候选id或null\","
            "\"reply_to_message_ids\":[\"实际回应消息id\"],\"reason\":\"简短原因\"}。")},
            {"role": "user", "content": f"场景：{scene.title if scene else ''}\n{scene.description if scene else ''}\n"
             f"幕后要求：{run.directive or '自然延续当前场景'}\n候选：\n" + "\n".join(rows) + f"\n已完成：{run.completed_replies}条\n真实共享公开记录：\n"}]
        from mrp.orchestrator.model_capacity import resolve_model_capacity
        from mrp.shared.prompt import estimate_tokens
        from mrp.orchestrator.scene_frame import _fit
        capacity_settings = settings.model_copy(update={"model": cfg.model, "model_provider": cfg.provider,
            "context_limit_override": settings.context_limit_override if cfg.model == settings.model else None})
        capacity = await resolve_model_capacity(capacity_settings, cfg.model, reply_max_tokens=600, base_url=cfg.base_url)
        available = None if capacity.input_limit is None else capacity.input_limit - sum(estimate_tokens(p["content"]) for p in prompt)
        if available is not None and available < 80:
            raise RuntimeError("调度模型容量不足以容纳候选人物与幕后要求，请调整辅助模型")
        kept, included, shortened = [], [], []
        omitted = []
        for message in reversed(public_messages[-8:]):
            line = f"{message.id} | {labels.get(message.actor, message.actor)}：{message.content}"
            tokens = estimate_tokens(line + "\n")
            if available is None or tokens <= available:
                kept.append(line); included.append(message.id)
                if available is not None: available -= tokens
            elif not kept:
                fitted = _fit(line, max(0, available - 16))
                kept.append(fitted + "〔调度材料节录；完整正文仍保留〕")
                included.append(message.id); shortened.append(message.id); available = 0
            else:
                omitted.append(message.id)
        prompt[-1]["content"] += "\n".join(reversed(kept)) or "暂无公开发言"
        trace = {"included_message_ids": list(reversed(included)), "shortened_message_ids": shortened,
            "omitted_message_ids": [m.id for m in public_messages[:-8]] + omitted,
            "capacity_source": capacity.source, "input_limit": capacity.input_limit}
        text, usage = await asyncio.to_thread(chat_text_with_usage, prompt, cfg,
            max_tokens=600, timeout=30, no_thinking=settings.thinking == "off", require_complete=True)
        try:
            data = extract_json(text)
        except Exception as exc:
            error = RuntimeError("发言调度返回了无法解析的结果")
            error.usage = usage
            raise error from exc
        if not isinstance(data, dict) or data.get("action") not in {"speak", "stop", "needs_user"}:
            error = RuntimeError("发言调度返回无效结果")
            error.usage = usage
            raise error
        chosen = data.get("speaker_id")
        if data["action"] == "speak" and chosen not in candidates:
            error = RuntimeError("发言调度选择了不在候选中的人物")
            error.usage = usage
            raise error
        replied = data.get("reply_to_message_ids") or []
        if not isinstance(replied, list) or any(not isinstance(mid, str) or mid not in included for mid in replied):
            error = RuntimeError("发言调度引用了未提供的消息")
            error.usage = usage
            raise error
        return Selection(data["action"], chosen, str(data.get("reason") or "")[:300], TokenUsage(**usage),
                         list(dict.fromkeys(replied)), trace)


class ScriptedConversationSelector:
    """Synthetic deterministic acceptance hook; never invokes a real model."""
    def __init__(self, script=None, usage=None):
        self.script = list(script or [])
        self.calls = []
        self.usage = usage or TokenUsage()

    async def select(self, **data):
        self.calls.append(data)
        item = self.script.pop(0) if self.script else "stop"
        if callable(item):
            item = item(data)
            if inspect.isawaitable(item):
                item = await item
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, Selection):
            return item
        return Selection(item if item in {"stop", "needs_user"} else "speak",
                         None if item in {"stop", "needs_user"} else item,
                         "合成调度", self.usage.model_copy())
