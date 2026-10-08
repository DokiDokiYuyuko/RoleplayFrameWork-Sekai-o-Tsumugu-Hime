"""Immutable generation material and a no-story-commit reply boundary.

The legacy turn kernel remains the adapter for prompt composition, streaming,
usage and hygiene. It receives an isolated baseline and must return a draft;
application services alone install that draft in story state.
"""
from dataclasses import dataclass
import json
from typing import Protocol
from mrp.shared.models import GroupActor, Message, SessionState, MemoryRecord


@dataclass(frozen=True)
class GenerationInput:
    baseline_json: str
    actor_json: str
    pending_json: str | None
    turn: int
    actor_kind: str
    frozen: bool = False
    memory_watermark: int | None = None
    memory_cutoff: str | None = None
    extensions_json: str = "{}"

    @classmethod
    def capture(cls, state, actor, turn, pending=None):
        # Prompt/history readers require generation presence and variant IDs,
        # not historic audit payloads, alternate prose or revision snapshots.
        excluded = {name: True for name in ("turn_runs", "conversation_runs", "generation_operations",
            "usage_records", "state_revisions", "story_events", "bookmarks", "director_log", "pending_director")}
        excluded["messages"] = {"__all__": {"generation_meta", "variants", "hygiene"}}
        material = state.model_dump(mode="json", exclude=excluded)
        for raw, message in zip(material["messages"], state.messages):
            raw["generation_meta"] = {} if message.generation_meta is not None else None
            raw["variants"] = [{"id": item.id, "content": "", "created_at": item.created_at.isoformat()}
                               for item in message.variants]
        return cls(json.dumps(material, ensure_ascii=False), actor.model_dump_json(),
            pending.model_dump_json() if pending is not None else None, turn,
            "group" if isinstance(actor, GroupActor) else "character",
            getattr(state, "_generation_frozen", False),
            getattr(state, "_generation_memory_watermark", None),
            getattr(state, "_generation_memory_cutoff", None),
            json.dumps({name: getattr(state, name) for name in (
                "_conversation_turn", "_conversation_directive", "_generation_memory_overrides",
                "_correction_origin", "_generation_policy_injections") if hasattr(state, name)},
                ensure_ascii=False, default=lambda value: value.model_dump(mode="json")))

    def baseline(self):
        state = SessionState.model_validate_json(self.baseline_json)
        if self.frozen:
            object.__setattr__(state, "_generation_frozen", True)
            object.__setattr__(state, "_generation_memory_watermark", self.memory_watermark)
            object.__setattr__(state, "_generation_memory_cutoff", self.memory_cutoff)
        for name, value in json.loads(self.extensions_json).items():
            if name == "_generation_memory_overrides":
                value = {key: MemoryRecord.model_validate(record) for key, record in value.items()}
            object.__setattr__(state, name, value)
        return state


@dataclass(frozen=True)
class GenerationResult:
    message_json: str

    def message(self):
        return Message.model_validate_json(self.message_json)


class ReplyGenerationPort(Protocol):
    async def generate(self, request: GenerationInput, args: tuple, options: dict) -> GenerationResult: ...

