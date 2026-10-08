"""Generation coordination shared by ordinary, group and corrected HTTP actions.

Models run on the staged runtime before the story transaction opens. The
application owns dependency protection and installation of generated drafts.
"""
from mrp.contracts.story import project_message
from mrp.shared.story_errors import RegenerationConflict
from mrp.application.ports import StoryGenerationPort, BranchRunner, RegenerationInput


class StoryGeneration:
    def __init__(self, generation: StoryGenerationPort):
        self.generation = generation

    async def regenerate(self, runner: BranchRunner, action, message_id, request: RegenerationInput, *, variant_index=None,
                         baseline_transform=None, protect_extra=None):
        async def protect(message_ids):
            if await self.generation.referenced_by_child(runner.state.meta.id, message_ids):
                raise RegenerationConflict("回应已被子世界线引用，请在该世界线内继续")
            if protect_extra is not None:
                await protect_extra(message_ids)
        return await self.generation.regenerate(runner, action, message_id, request,
            protect=protect, variant_index=variant_index, baseline_transform=baseline_transform)

    async def group_reply(self, runner: BranchRunner, group, idempotency_key):
        draft = await self.generation.generate_group(runner, group, idempotency_key)
        self.generation.install_message(runner, draft)
        await runner._emit("message.final", {"message": draft.model_dump(mode="json")})
        return project_message(draft)
