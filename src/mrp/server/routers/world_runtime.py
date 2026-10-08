"""Inspect a world with the production prompt planner without creating a story."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.orchestrator.session import SessionRunner
from mrp.orchestrator.context_plan import plan_context
from mrp.shared.models import Character, CharacterCard, Message, Scene, SessionMeta, SessionState, TurnContext
from mrp.shared.prompt import persona_from_card

router = APIRouter()


class PreviewWorldReq(BaseModel):
    character_id: str | None = None
    message: str = Field('', max_length=5000)


@router.post('/api/v1/worlds/{world_id}/runtime-preview')
async def preview_world(world_id: str, req: PreviewWorldReq, container: AppContainer = Depends(get_container)):
    world = container.worlds.get(world_id)
    if world is None:
        raise HTTPException(404, '世界不存在')
    if req.character_id and req.character_id not in container.characters:
        raise HTTPException(404, '角色不存在')
    character = (container.characters[req.character_id].model_copy(deep=True) if req.character_id
                 else Character(card=CharacterCard(name='设定检查角色')))
    container._apply_global_llm_settings(character)
    meta = SessionMeta(title='世界资料检查', character_ids=[character.id], memory_enabled=False,
        world_core_brief=world.core_brief, world_runtime_policy=world.runtime_policy,
        world_archive_records=[x.model_dump(mode='json') for x in world.archive_records])
    scene = Scene(title='设定检查', member_ids=[character.id])
    books = [container.lorebooks[x].model_copy(deep=True) for x in world.lorebook_ids if x in container.lorebooks]
    state = SessionState(meta=meta, characters=[character], lorebooks=books, scenes=[scene], active_scene_id=scene.id)
    if req.message:
        state.messages.append(Message(session_id=meta.id, seq=0, turn=1, actor='player', content=req.message))
    runner = SessionRunner(state, container.engine_manager, lorebooks=books, app_settings=container.settings)
    capacity = await runner.context_builder.model_capacity(character)
    injections = await runner.context_builder.build_injections(character, 1, visible=state.messages)
    ctx = TurnContext(session_id=meta.id, character_id=character.id, turn=1,
        visible_messages=state.messages, injections=injections, world_core_brief=world.core_brief,
        world_runtime_policy=world.runtime_policy, budget_tokens=capacity.input_limit,
        capacity_source=capacity.source, context_limit=capacity.context_limit, output_reserve=capacity.output_reserve,
        actor_labels={character.id: character.card.name, 'player': '玩家'},
        prompt_transforms=runner.context_builder.prompt_transforms())
    try:
        plan = plan_context(ctx, model=character.llm.model, persona=persona_from_card(character.card))
    except ValueError as exc:
        return {'world_id': world.id, 'revision': world.revision, 'runtime_policy': world.runtime_policy,
                'valid': False, 'error': str(exc), 'input_limit': capacity.input_limit, 'sources': []}
    kept = set(plan.prompt.included_entry_ids)
    sources = [{'id': x.entry_id, 'source': x.source, 'content': x.content, 'included': x.entry_id in kept,
                'reason': x.reason if x.entry_id in kept else plan.prompt.omitted_reasons.get(x.entry_id, '未提供')}
               for x in injections]
    injected_ids = {x.entry_id for x in injections}
    for record in world.archive_records:
        if record.id not in injected_ids:
            reason = ('作者资料，未选为故事用途' if record.visibility == 'private' else
                      '整理模式使用核心与词条，原稿只留存' if world.runtime_policy == 'compiled' else
                      '未被当前场景提及或类型尚不支持')
            sources.append({'id': record.id, 'source': 'archive', 'content': record.body,
                            'included': False, 'reason': f'{record.title}：{reason}'})
    if world.core_brief:
        sources.insert(0, {'id': 'world_core', 'source': 'world', 'content': world.core_brief,
                          'included': world.runtime_policy != 'raw',
                          'reason': '直接使用原文时不重复投递核心' if world.runtime_policy == 'raw' else '每轮使用的世界核心'})
    return {'world_id': world.id, 'revision': world.revision, 'runtime_policy': world.runtime_policy,
        'valid': True, 'input_limit': capacity.input_limit, 'estimated_tokens': plan.estimated_input_tokens,
        'capacity_source': capacity.source, 'prompt': persona_from_card(character.card) + '\n\n' + plan.prompt.text,
        'sources': sources,
        'notes': ['这是当前设定和测试话的输入检查，没有调用生成模型，也没有创建故事或记忆。']}
