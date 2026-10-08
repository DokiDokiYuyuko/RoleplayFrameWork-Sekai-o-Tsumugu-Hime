"""Isolated UI fixture: fictional stories, fake engine, disposable data root."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


async def main(port, history_messages=120):
    import uvicorn
    from mrp.engines.fake import FakeEngine
    from mrp.server.app import create_app
    from mrp.server.container import AppContainer, AppContainerConfig
    from mrp.server.routers.messages import SendMessageReq
    from mrp.shared.models import Character, CharacterCard, Message, MemoryRecord
    from mrp.worlds.schema import World
    # Workshop HTTP endpoints also stay synthetic. Never use the configured gateway.
    from mrp.server.routers import workshop as character_workshop
    def synthetic_character_call(messages):
        import time
        if "字段改写器" in messages[0]["content"]:
            if "__SLOW_AI__" in messages[-1]["content"]:
                time.sleep(1)
            return "合成精修候选，保留人物身份。"
        if messages[-1]["content"] == "__FAIL_TRIAL__":
            raise RuntimeError("合成试聊失败")
        turns = sum(row["role"] == "user" for row in messages)
        return f"合成口吻回应：第 {turns} 轮；记得你说的 {messages[1]['content']}。"
    character_workshop.llm_call = lambda container: synthetic_character_call

    with tempfile.TemporaryDirectory(prefix="mrp-development-preview-") as temporary:
        data = Path(temporary) / "data"
        os.environ["MRP_DATA_ROOT"] = str(data)
        os.environ["MRP_FAKE_ENGINE"] = "1"
        os.environ["MRP_PORT"] = str(port)
        container = AppContainer(data_root=data, config=AppContainerConfig(fake_mode=True),
                                 web_dist=ROOT / "src" / "web" / "dist")
        container.settings.context_limit_override = 64000
        # Synthetic authoring workers exercise real HTTP jobs/review/commits;
        # every fixture refuses to use a configured upstream model.
        authoring_calls = {'organize': 0, 'lorebook': 0}
        async def synthetic_import(prompt, job_id, attempt):
            authoring_calls['organize'] += 1
            from mrp.asset_import.service import CharacterInput
            source = container.asset_imports.source(job_id)
            evidence = source.strip()[:60]
            if '仅返回 {"core_brief":string}' in prompt:
                return json.dumps({"core_brief": "合成海岸世界，旅途遵循潮汐。"}, ensure_ascii=False)
            if 'mes_example' in prompt and 'personality' in prompt:
                payload = CharacterInput(name="合成整理守卫", description="守护海岸，习惯先观察再行动。", appearance="穿着朴素的深色外套。", traits="熟悉潮汐。", personality="说话平静。").model_dump(mode='json')
            else:
                payload = {"title": "合成海岸世界", "description": "用于离线创作验收。", "core_brief": "合成海岸世界，旅途遵循潮汐。"}
            result = {"payload": payload, "evidence": evidence}
            if 'addition_text' in prompt:
                result.update(addition_text="新增合成设定：冬季节庆时，人们在港口点起灯笼。", added_facts=["冬季港口灯笼节。"])
            return json.dumps(result, ensure_ascii=False)
        async def synthetic_lore(**kwargs):
            authoring_calls['lorebook'] += 1
            snapshot = json.loads(Path(kwargs['task_file']).read_text(encoding='utf-8'))
            source = snapshot['sources'][0]
            quote = source['content'][:80]
            return json.dumps({"entries": [{"payload": {"keys": ["潮晶"], "comment": "合成潮晶规则", "content": "合成潮晶规则：只有满月高潮时可以蓄能，黎明后能量散尽。"},
                "source_refs": [{"source_id": source['id'], "quote": quote}],
                "positive_examples": ["看看潮晶现在能否蓄能。"], "negative_examples": ["我们回家。"],
                "rationale": "合成触发规则验收。", "risk_notes": []}]}, ensure_ascii=False)
        container.asset_imports._call = synthetic_import
        container.lorebook_generation.worker.run = synthetic_lore
        class PreviewEngine(FakeEngine):
            fail = True
            async def start(self, character):
                self.character = character
                await super().start(character)
            async def generate(self, ctx, *, on_delta=None):
                if self.fail and self.character.card.name == "渡桥旅人":
                    raise RuntimeError("合成失败：用于验证已保存的回应恢复")
                self.replies = [f"{self.character.card.name}收好地图：这次从北岸出发，我们沿着灯塔寻找桥。第 {self._turns + 1} 次合成回应。"] * (self._turns + 1)
                return await super().generate(ctx, on_delta=on_delta)
        container.engine_manager._factory = PreviewEngine
        guard = Character(card=CharacterCard(name="灯塔守卫", description="熟悉海岸，旧设定：不能游泳。", mes_example="{{char}}：{{user}}，先看地图再出发。"))
        traveller = Character(card=CharacterCard(name="渡桥旅人", description="带着绳索与旅行日记。"))
        await container.save_character(guard)
        await container.save_character(traveller)
        world = World(title="示例与灯塔", core_brief="北岸的桥暂时关闭。")
        await container.world_registry.save(world)
        runner = await container.create_session("合成验收 · 北岸旅程", [guard.id, traveller.id], "玩家名：旅客", [], world_id=world.id)
        runner.state.meta.memory_enabled = False
        for i in range(history_messages):
            msg = runner._append_message(Message(session_id=runner.state.meta.id, seq=runner.state.next_seq(),
                turn=i // 2 + 1, actor="player" if i % 2 == 0 else guard.id,
                content=f"合成历史 {i + 1}：我们在灯塔核对路线，约定明天归还小铜铃。"))
            runner._record_message_revision(msg)
        await container.persist_session(runner)
        result = await container.turn_runs.send(runner, SendMessageReq(content="带好地图，我们讨论北岸的路线。", mentions=[guard.id, traveller.id], reply_mode="serial", client_message_id="msg-aaaaaaaaaaaa"))
        PreviewEngine.fail = False
        if result["turn_run"]["status"] != "failed":
            raise RuntimeError("The fixture did not create an interrupted turn")
        anchor = runner.state.messages[-1]
        container.memory_store.add(MemoryRecord(character_id=guard.id, session_id=runner.state.meta.id,
            kind="manual", category="unfinished", matter_status="open", content="明天归还小铜铃。",
            source_message_ids=[anchor.id], source_fingerprints={anchor.id: anchor.fingerprint}, important=True))
        updated = guard.model_copy(deep=True)
        updated.card.description = "熟悉海岸，新版修订：擅长游泳，但不会替旅客决定路线。"
        updated.revision += 1
        await container.save_character(updated)
        world.core_brief = "北岸的桥已由守桥人检查，可以步行通过。"
        world.revision += 1
        await container.world_registry.save(world)
        print(json.dumps({"url": f"http://127.0.0.1:{port}", "story_id": runner.state.meta.story_id,
            "branch_id": runner.state.meta.id, "characters": [guard.id, traveller.id]}, ensure_ascii=True), flush=True)
        try:
            app = create_app(container)
            @app.get("/api/v1/acceptance-fixture")
            async def fixture_identity():
                return {"fixture": "mrp.development.synthetic", "branch_id": runner.state.meta.id,
                        "story_id": runner.state.meta.story_id, "guard_id": guard.id,
                        "authoring_calls": authoring_calls}
            app.router.routes.insert(0, app.router.routes.pop())
            from mrp.storage.personal_library import backup_library
            offline = Path(temporary) / "offline-library"
            (offline / "sessions").mkdir(parents=True)
            (offline / "sessions" / "sess-synthetic.json").write_text('{"story":"synthetic offline backup"}', encoding="utf-8")
            archive = Path(temporary) / "offline-library.zip"
            backup_library(offline, archive, offline_confirmed=True)
            @app.get("/api/v1/acceptance-library-backup")
            async def fixture_backup():
                from fastapi.responses import Response
                return Response(archive.read_bytes(), media_type="application/zip")
            app.router.routes.insert(0, app.router.routes.pop())
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
            await server.serve()
        finally:
            await container.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--history-messages", type=int, default=120, choices=range(120, 10001))
    args = parser.parse_args()
    asyncio.run(main(args.port, args.history_messages))
