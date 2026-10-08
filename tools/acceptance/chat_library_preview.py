"""Disposable real HTTP API fixture for chat/library acceptance; no upstream calls."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


async def main(port):
    import uvicorn
    from mrp.server.app import create_app
    from mrp.server.container import AppContainer, AppContainerConfig
    from mrp.shared.models import Character, CharacterAuthoringSource, CharacterCard, Lorebook, LorebookEntry, Message
    from mrp.simple_chat import PlainChat, PlainMessage
    from mrp.worlds.schema import World
    with tempfile.TemporaryDirectory(prefix='mrp-chat-library-acceptance-') as temporary:
        data = Path(temporary) / 'data'
        os.environ['MRP_DATA_ROOT'] = str(data)
        os.environ['MRP_FAKE_ENGINE'] = '1'
        os.environ['MRP_PORT'] = str(port)
        container = AppContainer(data_root=data, config=AppContainerConfig(fake_mode=True), web_dist=ROOT/'src/web/dist')
        private_marker = 'SYNTHETIC-SHARE-PRIVATE-QUOTE'
        first_book = Lorebook(name='星海设定', tags=['星海'], entries=[
            LorebookEntry(uid=1,keys=['灯塔'],content='合成灯塔设定。', extensions={
                'mrp.runtime_scope': 'shared', 'mrp.archive_sources': [{'source_id': 'synthetic-source', 'quote': private_marker}],
                'mrp.generated': {'job_id': 'synthetic-job', 'before_payload': private_marker},
                'vendor.custom_rule': {'retain': True}}),
            LorebookEntry(uid=9,content=private_marker + '-author-body',constant=True,
                          extensions={'mrp.runtime_scope': 'author'}),
        ])
        second_book = Lorebook(name='山谷设定', tags=['草原'])
        await container.save_lorebook(first_book)
        await container.save_lorebook(second_book)
        world = World(title='合成示例', lorebook_ids=[first_book.id])
        await container.world_registry.save(world)
        guard = Character(card=CharacterCard(name='合成向导', description='合成角色验收说明。', tags=['星海']), aliases=['渡鸦'],
                          bound_lorebook_ids=[first_book.id], source_world_id=world.id,
                          authoring_source=CharacterAuthoringSource(text='只允许私密迁移的合成创作原稿。'))
        guard.card.extensions['character_book'] = {'name': '合成内嵌书', 'entries': [
            {'id': 21, 'content': '合成内嵌公开设定。', 'extensions': {
                'mrp.archive_source': {'quote': private_marker}, 'vendor.custom_rule': {'retain': True}}},
            {'id': 29, 'content': private_marker + '-embedded-author', 'extensions': {'mrp.runtime_scope': 'author'}},
        ]}
        traveller = Character(card=CharacterCard(name='合成旅人', description='合成列表第二项。', tags=['草原']))
        await container.save_character(guard)
        await container.save_character(traveller)
        runner = await container.create_session('合成操作验收', [guard.id,traveller.id], '玩家名：合成玩家', [])
        runner.state.meta.memory_enabled = False
        kinds = [('player','roleplay'),(guard.id,'roleplay'),('player','inner'),('player','scene'),('director','scene'),('director','system_event'),(guard.id,'ooc'),('director','system_event'),(guard.id,'roleplay')]
        for index,(actor,kind) in enumerate(kinds):
            message = runner._append_message(Message(session_id=runner.state.meta.id, seq=runner.state.next_seq(), turn=1,
                actor=actor, kind=kind, content='[群体]\n合成群体状态\n合成群体记录。' if index == 7 else f'合成气泡 {index+1}：在灯塔核对路线，保留消息内容与现有候选。'))
            runner._record_message_revision(message)
        await container.persist_session(runner)
        chat = PlainChat(title='合成聊天确认',gateway='https://example.invalid/api/v1',model='synthetic/model',messages=[
            PlainMessage(role='user',content='合成问题'),
            PlainMessage(role='assistant',content='合成候选一',variants=['合成候选一','合成候选二'],active_variant=0),
            PlainMessage(role='user',content='合成后续问题'),
            PlainMessage(role='assistant',content='合成后续回答',variants=['合成后续回答'],active_variant=0)])
        await container.simple_chats._save(chat)
        app = create_app(container)
        @app.get('/api/v1/chat-library-fixture')
        async def fixture():
            return {'fixture':'mrp.chat-library.synthetic','story_id':runner.state.meta.story_id,'branch_id':runner.state.meta.id,
                    'character_ids':[guard.id,traveller.id], 'book_ids':[first_book.id,second_book.id], 'world_id':world.id,
                    'chat_id':chat.id,'message_ids':[item.id for item in runner.state.messages],
                    'plain_message_ids':[item.id for item in chat.messages], 'private_marker': private_marker}
        app.router.routes.insert(0,app.router.routes.pop())
        try:
            server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
            await server.serve()
        finally:
            await container.aclose()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18247)
    asyncio.run(main(parser.parse_args().port))
