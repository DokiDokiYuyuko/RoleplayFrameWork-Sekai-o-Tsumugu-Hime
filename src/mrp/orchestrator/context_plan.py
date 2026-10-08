"""Frozen prompt plan shared by inspection and the outgoing engine request."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mrp.shared.models import ComposedPrompt, TurnContext
from mrp.shared.prompt import compose_prompt, estimate_tokens


@dataclass(frozen=True)
class ContextPlanSegment:
    id: str
    kind: str
    source_id: str
    original_tokens: int
    sent_tokens: int
    disposition: str
    reason: str
    content_hash: str
    section: str = ""


@dataclass(frozen=True)
class ContextPlan:
    plan_id: str
    model: str
    capacity_source: str
    capacity_limit: int | None
    output_reserve: int | None
    input_limit: int | None
    estimated_input_tokens: int
    segments: tuple[ContextPlanSegment, ...]
    prompt: ComposedPrompt


def plan_context(ctx: TurnContext, *, model: str, persona: str) -> ContextPlan:
    object.__setattr__(ctx, "_planned_persona", persona)
    composed = compose_prompt(ctx)
    included = set(composed.included_entry_ids)
    kept_messages = set(composed.included_message_ids)
    segments = []
    for inj in ctx.injections:
        tokens = estimate_tokens(inj.content)
        kept = inj.entry_id in included
        segments.append(ContextPlanSegment(
            id=inj.entry_id, kind="injection", source_id=inj.source,
            original_tokens=tokens, sent_tokens=tokens if kept else 0,
            disposition="kept" if kept else "omitted",
            reason=inj.reason if kept else composed.omitted_reasons.get(inj.entry_id, "输入容量不足"),
            content_hash=hashlib.sha256(inj.content.encode("utf-8")).hexdigest(),
            section=composed.entry_sections.get(inj.entry_id, inj.placement if kept else "omitted"),
        ))
    for message in ctx.visible_messages:
        tokens = estimate_tokens(message.content)
        kept = message.id in kept_messages
        segments.append(ContextPlanSegment(
            id=message.id, kind="message", source_id=message.actor,
            original_tokens=tokens, sent_tokens=tokens if kept else 0,
            disposition="kept" if kept else "omitted",
            reason="角色可见" if kept else composed.omitted_reasons.get(message.id, "输入容量不足"),
            content_hash=hashlib.sha256(message.content.encode("utf-8")).hexdigest(),
            section=composed.message_sections.get(message.id, "omitted"),
        ))
    payload_hash = hashlib.sha256((model + "\n" + persona + "\n" + composed.text).encode("utf-8")).hexdigest()
    return ContextPlan(
        plan_id=payload_hash, model=model, capacity_source=ctx.capacity_source,
        capacity_limit=ctx.context_limit, output_reserve=ctx.output_reserve,
        input_limit=ctx.budget_tokens,
        estimated_input_tokens=estimate_tokens(persona) + composed.total_tokens + 32,
        segments=tuple(segments), prompt=composed,
    )
