"""Compatibility adapter for the existing prompt, model and usage kernel.

Only this outer adapter can access engine.r. The application contract receives
serialized immutable input and returns a draft without publishing story state.
"""
from typing import Awaitable, Callable
from mrp.shared.models import Character, GroupActor, Message
from mrp.application.reply_generation import GenerationInput, GenerationResult, ReplyGenerationPort


class LegacyTurnKernelAdapter:
    """Transitional adapter: still owns engine.r for existing runtime facilities.

    The port freezes generation material and forbids target mutation. Moving
    prompt/model/usage dependencies off runner is a subsequent migration;
    this adapter does not claim complete separation of the legacy kernel.
    """
    def __init__(self, kernel: Callable[..., Awaitable[Message]], engine):
        self.kernel, self.engine = kernel, engine

    async def generate(self, request: GenerationInput, args: tuple, options: dict) -> GenerationResult:
        baseline = request.baseline()
        isolated_actor = (GroupActor if request.actor_kind == "group" else Character).model_validate_json(request.actor_json)
        call_options = {**options, "defer_commit": True, "emit_final": False}
        if request.pending_json is not None:
            call_options["pending_override"] = Message.model_validate_json(request.pending_json)
        call_options["state_snapshot" if request.actor_kind == "group" else "context_state"] = baseline
        message = await self.kernel(self.engine, isolated_actor, request.turn, *args, **call_options)
        return GenerationResult(message.model_dump_json())


async def generate_reply(kernel: Callable[..., Awaitable[Message]], engine, actor, turn, args, options):
    if options.get("target") is not None:
        raise ValueError("Deferred generation cannot mutate an existing story message")
    source = options.get("context_state") or options.get("state_snapshot") or engine.r.state
    if source is engine.r.state:
        source = source.model_copy(deep=False)
        source.lorebooks = list(engine.r.lorebooks)
    request = GenerationInput.capture(source, actor, turn, options.get("pending_override"))
    generator: ReplyGenerationPort = LegacyTurnKernelAdapter(kernel, engine)
    return await generator.generate(request, args, options)

