"""应用容器（AppContainer）：服务层的可注入装配体（W1 波 1 重构 + 波 2 接 W2 storage）。

设计约束：
- **不读全局 env**：容器只接受显式参数；env 兜底统一在工厂 `build_container_from_env()` 里做。
  多实例（`create_app(container_a)` / `create_app(container_b)`）互不共享状态：注册表、
  引擎管理器、事件总线、记忆库、运行器缓存全在实例上。
- 数据根目录（data_root）与前端静态目录（web_dist）均可注入，打包/嵌入时可替换默认值。
- 存储统一走 `mrp.storage`（原子写 + 索引 + 线程池 IO）；读写方法多为 `async def`。
- 生命周期：`aclose()` 取消预热任务 → 关全部 runner → 停引擎 → 关 SQLite。
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mrp.engines.dsh.engine import DshEngine
from mrp.engines.request_archive import RequestArchive
from mrp.simple_chat import PlainChatService
from mrp.engines.dsh.process import EngineManager
from mrp.engines.fake import FakeEngine
from mrp.engines.openrouter import OpenRouterEngine
from mrp.orchestrator.memory import MemoryStore, default_summarizer
from mrp.orchestrator.session import SessionRunner
from mrp.scenarios.repository import ScenarioRepository
from mrp.scenarios.schema import ScenarioPackage
from mrp.scenarios.service import instantiate as instantiate_scenario
from mrp.server.sse import EventBus
from mrp.settings import AppSettings, load_settings, provider_profile_id
from mrp.tts import FishSpeechManager, VoiceProfileStore
from mrp.shared.models import Character, Lorebook, Message, Scene, SessionMeta, SessionState
from mrp.storage import AppPaths, CharacterRegistry, LorebookRegistry, WorldRegistry, SaveRepo, SessionRepo, default_data_root, validate_data_root
from mrp.worlds.schema import World
from mrp.asset_import.service import ImportService

logger = logging.getLogger("mrp.server")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = default_data_root()
DEFAULT_WEB_DIST = PROJECT_ROOT / "src" / "web" / "dist"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1"


@dataclass
class AppContainerConfig:
    """env/宿主可注入的行为开关（默认值 = 生产默认，无 env 依赖）。"""

    fake_mode: bool = False
    fake_reply: str = ""          # MRP_FAKE_REPLY：冒烟用长文本
    fake_judge: str = ""          # MRP_FAKE_JUDGE=flag/strict
    fake_director: str = ""       # MRP_FAKE_DIRECTOR=switch/pick
    fake_padding: str = ""        # MRP_FAKE_PADDING=固定文本
    model: str = "deepseek/deepseek-v4-flash"  # MRP_MODEL
    auxiliary_model: str = "deepseek/deepseek-v4-flash"  # MRP_AUX_MODEL
    base_url: str = ""            # OPENROUTER_BASE_URL（空 → 调用点回落 DEFAULT_OPENROUTER_URL）
    api_key_env: str = "OPENROUTER_API_KEY"
    runner_cache: int = 4         # MRP_RUNNER_CACHE：runners LRU 上限

    @classmethod
    def from_env(cls) -> AppContainerConfig:
        """env → 配置（唯一读全局 env 的地方；行为与旧址 World() 一致）。"""
        try:
            runner_cache = int(os.environ.get("MRP_RUNNER_CACHE", "4"))
        except ValueError:
            runner_cache = 4
        return cls(
            fake_mode=os.environ.get("MRP_FAKE_ENGINE", "") not in ("", "0", "false"),
            fake_reply=os.environ.get("MRP_FAKE_REPLY", ""),
            fake_judge=os.environ.get("MRP_FAKE_JUDGE", ""),
            fake_director=os.environ.get("MRP_FAKE_DIRECTOR", ""),
            fake_padding=os.environ.get("MRP_FAKE_PADDING", ""),
            model=os.environ.get("MRP_MODEL", "deepseek/deepseek-v4-flash"),
            auxiliary_model=os.environ.get("MRP_AUX_MODEL", "") or os.environ.get(
                "MRP_MODEL", "deepseek/deepseek-v4-flash"
            ),
            base_url=os.environ.get("OPENROUTER_BASE_URL", ""),
            runner_cache=max(1, runner_cache),
        )

class AppContainer:
    """应用级容器：注册表 + 运行器 + 持久化 + 引擎/记忆/LLM 组件装配。"""

    def __init__(
        self,
        data_root: Path | None = None,
        settings: AppSettings | None = None,
        *,
        config: AppContainerConfig | None = None,
        web_dist: Path | None = None,
    ) -> None:
        # Construction opens schemas and recovers journals before ASGI lifespan.
        # Fence those writes as well; lifespan takes its own long-lived lease.
        from mrp.storage.data_lease import DataRootLease
        root = validate_data_root(data_root if data_root is not None else default_data_root(), PROJECT_ROOT)
        with DataRootLease(root, purpose="server"):
            self._initialize(root, settings, config=config, web_dist=web_dist)

    def _initialize(
        self,
        data_root: Path | None = None,
        settings: AppSettings | None = None,
        *,
        config: AppContainerConfig | None = None,
        web_dist: Path | None = None,
    ) -> None:
        cfg = config if config is not None else AppContainerConfig()
        self.config = cfg
        self.settings = settings if settings is not None else AppSettings(
            gateway=cfg.base_url or DEFAULT_OPENROUTER_URL,
            model=cfg.model,
            auxiliary_model=cfg.auxiliary_model,
        )
        self._refresh_llm_config()
        self.data_root = validate_data_root(data_root if data_root is not None else default_data_root(), PROJECT_ROOT)
        # 波 2：目录布局唯一事实来源 = mrp.storage.AppPaths（下列 *_dir 为兼容属性）
        self.paths = AppPaths(self.data_root).ensure()
        self.asset_imports = ImportService(self)
        from mrp.lorebook_generation.service import LorebookGenerationService
        self.lorebook_generation = LorebookGenerationService(self)
        from mrp.world_organize.service import WorldOrganizeService
        self.world_organize = WorldOrganizeService(self)
        from mrp.card_discovery.service import CardDiscoveryService
        from mrp.card_inspiration.service import CardInspirationService
        self.card_discovery = CardDiscoveryService()
        self.card_inspiration = CardInspirationService(self, self.card_discovery)
        self.tts_voices = VoiceProfileStore(self.data_root)
        self.tts_manager = FishSpeechManager(self.settings.tts)
        # D2：前端静态目录可注入（打包/嵌入时替换）；None → 不托管静态资源
        self.web_dist = Path(web_dist) if web_dist is not None else None

        self.bus = EventBus()
        # 波 2：注册表/仓储（读写原子 + 索引 + 线程池 IO）
        self.character_registry = CharacterRegistry(self.paths)
        self.lorebook_registry = LorebookRegistry(self.paths)
        self.world_registry = WorldRegistry(self.paths)
        self.sessions = SessionRepo(self.paths, sqlite_new_stories=True)
        self.saves = SaveRepo(self.paths)
        self.scenarios = ScenarioRepository(self.paths)
        from mrp.storage.prompt_presets import PromptPresetStore
        self.prompt_presets = PromptPresetStore(self.data_root)
        from mrp.storage.story_backups import StoryBackupService
        self.story_backups = StoryBackupService(self)
        from mrp.storage.story_search import StorySearchIndex
        self.story_search = StorySearchIndex(self.data_root)
        # B3：runners 带上限的 LRU（曾被 list_sessions 全量灌入 → 内存/句柄放大）
        self.runners: OrderedDict[str, SessionRunner] = OrderedDict()
        from mrp.application.branch_runtime import BranchRuntimeRegistry
        self.branch_runtimes = BranchRuntimeRegistry()
        from mrp.application.branch_commit import BranchCommit
        from mrp.server.application_adapters import (BranchCommitAdapter, MemoryRepositoryAdapter,
            MemoryGenerationAdapter, BackgroundRuntimeAdapter, StoryGenerationAdapter,
            DirectorCheckpointAdapter, CommandRepositoryAdapter)
        self.branch_commit = BranchCommit(BranchCommitAdapter(self))
        from mrp.application.memory_commands import MemoryCommands
        self.memory_commands = MemoryCommands(MemoryRepositoryAdapter(self),
            MemoryGenerationAdapter(self), BackgroundRuntimeAdapter(self), self.branch_commit)
        from mrp.application.story_generation import StoryGeneration
        self.story_generation = StoryGeneration(StoryGenerationAdapter(self))
        from mrp.application.checkpoint_commands import CheckpointCommands
        self.checkpoint_commands = CheckpointCommands(CommandRepositoryAdapter(self),
            DirectorCheckpointAdapter(self), self.branch_commit)
        self.runner_cache = cfg.runner_cache
        self._warm_tasks: set[asyncio.Task] = set()  # R28.2 引擎预热后台任务
        # 注：属性名保持 `_fake_mode`（现有测试会临时改它来单测引擎工厂分派）
        self._fake_mode = cfg.fake_mode
        self._fake_reply = cfg.fake_reply
        self.request_archive = RequestArchive(self.data_root)
        self.engine_manager = EngineManager(engine_factory=self._make_engine)
        self.simple_chats = PlainChatService(
            self.data_root, lambda: self.settings, self.bus, self.request_archive
        )
        self.memory_store = MemoryStore(
            db_path=self.paths.memory_dir / "memory.db",
            mirror_dir=self.paths.memory_dir,
            embedding=None,  # 向量嵌入未接线（原 MRP_EMBEDDINGS_URL 为死链，波 2 移除）
            vec_dim=64,
        )
        self.sessions.recover_memory_jobs()
        from mrp.application.story_lifecycle import StoryLifecycle
        from mrp.server.lifecycle_adapters import lifecycle_ports, creation_ports
        self.story_lifecycle = StoryLifecycle(lifecycle_ports(self))
        self.creation_ports = creation_ports(self)
        from mrp.orchestrator.worldline import WorldlineService
        self.worldlines = WorldlineService(self)
        # M12-R41 辅助候选（手动触发）：fake 模式用确定性生成器（冒烟零成本）；真模式走便宜通道
        from mrp.orchestrator.assist import AssistGenerator, FakeAssistGenerator
        from mrp.orchestrator.group_actors import GroupResponder

        self.assist_generator = (
            FakeAssistGenerator() if cfg.fake_mode else AssistGenerator(network=True)
        )
        from mrp.orchestrator.writing_assistant import WritingGenerator
        self.writing_generator = WritingGenerator(network=not cfg.fake_mode, fake=cfg.fake_mode)
        self.group_responder = GroupResponder(self)
        # R34 输出卫生校验器注入：
        # - MRP_FAKE_JUDGE=flag/strict → FakeJudge（测试/冒烟，零成本）
        # - fake 引擎模式且未显式指定 → 无 judge（fail-open 路径）
        # - 真模式 → HygieneJudge（使用设置页配置的辅助模型）
        self.hygiene_judge = None
        if cfg.fake_judge in ("flag", "strict"):
            from mrp.orchestrator.hygiene import FakeJudge

            self.hygiene_judge = FakeJudge(mode=cfg.fake_judge)
        elif not cfg.fake_mode:
            from mrp.orchestrator.hygiene import HygieneJudge

            self.hygiene_judge = HygieneJudge()
        # R35 LLM 导演注入（MRP_FAKE_DIRECTOR=switch|pick → FakeDirectorJudge；
        # fake 引擎模式默认 None → fail-open 回退 v1 轮盘）
        self.director_judge = None
        self.transition_llm = None
        if cfg.fake_director in ("switch", "pick"):
            from mrp.orchestrator.director_llm import FakeDirectorJudge

            self.director_judge = FakeDirectorJudge(mode=cfg.fake_director)
        elif not cfg.fake_mode:
            from mrp.llm import chat_text, director_config
            from mrp.orchestrator.director_llm import DirectorJudge

            self.director_judge = DirectorJudge()
            dcfg = director_config()
            self.transition_llm = lambda messages: chat_text(
                messages, dcfg, max_tokens=256, timeout=8.0, no_thinking=True
            )
        # R36 记忆分层注入：真模式 → 便宜模型网络；fake → 本地确定性降级（零成本）
        from mrp.orchestrator.memory_v2 import EpisodicConsolidator, SceneSummarizer

        self.episodic_consolidator = EpisodicConsolidator(network=not cfg.fake_mode)
        self.scene_summarizer = SceneSummarizer(network=not cfg.fake_mode)
        # R37.4 垫场生成器（真模式 → 便宜模型；MRP_FAKE_PADDING → 固定文本；fake 默认跳过）
        from mrp.orchestrator.padding import PaddingGenerator

        if cfg.fake_padding:
            self.padding_gen = PaddingGenerator(llm_call=lambda messages: cfg.fake_padding)
        else:
            self.padding_gen = PaddingGenerator(network=not cfg.fake_mode)
        self.summarizer = default_summarizer(
            base_url=self.config.base_url,
            api_key_env=cfg.api_key_env,
            model=self.config.auxiliary_model,
            provider=self.settings.auxiliary_provider,
            provider_allow_fallbacks=self.settings.provider_allow_fallbacks,
        )
        from mrp.storage.usage_ledger import UsageLedger
        self.usage_ledger = UsageLedger(self.data_root)
        self._load_all()
        self.asset_imports.recover_commits()
        self.lorebook_generation.recover_commits()
        self.world_organize.recover_commits()
        from mrp.orchestrator.conversation_runs import ConversationRuns
        self.conversation_runs = ConversationRuns(self)
        from mrp.orchestrator.turn_runs import TurnRuns
        self.turn_runs = TurnRuns(self)

    @property
    def fake_mode(self) -> bool:
        """是否 fake 引擎模式（设置页 info / 测试断言用；实际开关是 `_fake_mode`）。"""
        return self._fake_mode

    @property
    def api_key_configured(self) -> bool:
        return bool(self.settings.api_key)

    def _refresh_llm_config(self) -> None:
        """Make saved settings the active defaults for all LLM call paths."""
        self.config.model = self.settings.model
        self.config.auxiliary_model = self.settings.auxiliary_model or self.settings.model
        self.config.base_url = self.settings.gateway.rstrip("/")
        if self.settings.api_key:
            # 持久存储只在 data/settings.json；环境变量仅作为本进程现有 LLM 客户端的运行时桥接。
            os.environ[self.config.api_key_env] = self.settings.api_key
        else:
            os.environ.pop(self.config.api_key_env, None)
        os.environ["MRP_MODEL"] = self.config.model
        os.environ["MRP_AUX_MODEL"] = self.config.auxiliary_model
        os.environ["MRP_MODEL_PROVIDER"] = self.settings.model_provider
        os.environ["MRP_AUXILIARY_PROVIDER"] = self.settings.auxiliary_provider
        os.environ["MRP_PROVIDER_ALLOW_FALLBACKS"] = str(
            self.settings.provider_allow_fallbacks
        ).lower()
        os.environ["OPENROUTER_BASE_URL"] = self.config.base_url
        self.summarizer = default_summarizer(
            base_url=self.config.base_url,
            api_key_env=self.config.api_key_env,
            model=self.config.auxiliary_model,
            provider=self.settings.auxiliary_provider,
            provider_allow_fallbacks=self.settings.provider_allow_fallbacks,
        )
        if getattr(self, "transition_llm", None) is not None:
            from mrp.llm import chat_text, director_config

            dcfg = director_config()
            self.transition_llm = lambda messages: chat_text(
                messages, dcfg, max_tokens=256, timeout=8.0, no_thinking=True
            )

    def _apply_global_llm_settings(self, character: Character) -> None:
        """Resolve inherited defaults while retaining explicit character choices."""
        from mrp.shared.models import ModelConfig
        default = ModelConfig()
        cfg = character.llm
        if cfg.inherit_model is None:
            cfg.inherit_model = not cfg.model.strip() or cfg.model == default.model
        if cfg.inherit_base_url is None:
            cfg.inherit_base_url = not cfg.base_url.strip() or cfg.base_url == default.base_url
        if cfg.inherit_model:
            cfg.model = self.config.model
        if cfg.inherit_base_url:
            cfg.base_url = self.config.base_url
        cfg.base_url = cfg.base_url.rstrip("/")
        if cfg.base_url == self.config.base_url.rstrip("/"):
            cfg.api_key_env = self.config.api_key_env
        else:
            # Use the key saved for this gateway, never the currently active key.
            import hashlib
            profile = provider_profile_id(cfg.base_url)
            env_name = "MRP_ROLE_GATEWAY_" + hashlib.sha256(profile.encode()).hexdigest()[:16].upper()
            key = self.settings.provider_api_keys.get(profile, "")
            if key:
                os.environ[env_name] = key
            else:
                os.environ.pop(env_name, None)
            cfg.api_key_env = env_name
        cfg.effective_provider = (
            self.settings.model_provider
            if cfg.model == self.config.model and cfg.base_url == self.config.base_url.rstrip("/")
            else ""
        )

    def _make_engine(self):
        """引擎工厂（R48）：每次新建引擎时按**最新设置**分派；fake 模式最高优先。

        EngineManager 在每次新建（或重建）引擎时调用本方法——设置页 PATCH 后
        shutdown_all 清空引擎字典，下个回合即按新设置重建。

        思考档：原生直连支持真关闭（reasoning.enabled=false，实测 4/4 零思考）；
        DSH 的 "off" 会被上游不稳定地忽略（实测 3 次仅 1 次生效），故 DSH 固定低档，
        需要彻底关闭请切原生直连（设置页有提示）。
        """
        if self._fake_mode:
            return FakeEngine(replies=[self._fake_reply] if self._fake_reply else None)
        if self.settings.engine == "openrouter":
            return OpenRouterEngine(
                thinking=self.settings.thinking == "on",
                provider=self.settings.model_provider,
                provider_allow_fallbacks=self.settings.provider_allow_fallbacks,
                request_archive=self.request_archive,
                generation=self.settings.generation,
            )
        return DshEngine(
            reasoning_effort="low",
            provider=self.settings.model_provider if provider_profile_id(self.settings.gateway) == "openrouter" else "",
            provider_allow_fallbacks=self.settings.provider_allow_fallbacks,
            request_archive=self.request_archive,
            generation=self.settings.generation,
        )

    # ---------- 持久化 ----------

    # ---------- 目录（兼容属性：委托 AppPaths，routers 用法零改） ----------

    @property
    def characters_dir(self) -> Path:
        return self.paths.characters_dir

    @property
    def lorebooks_dir(self) -> Path:
        return self.paths.lorebooks_dir

    @property
    def sessions_dir(self) -> Path:
        return self.paths.sessions_dir

    @property
    def saves_dir(self) -> Path:
        return self.paths.saves_dir

    @property
    def memories_dir(self) -> Path:
        return self.paths.memory_dir

    # ---------- 注册表视图（写路径走 registry，读经同一 dict） ----------

    @property
    def characters(self) -> dict[str, Character]:
        return self.character_registry.by_id

    @property
    def lorebooks(self) -> dict[str, Lorebook]:
        return self.lorebook_registry.by_id

    @property
    def worlds(self) -> dict[str, World]:
        return self.world_registry.by_id

    # ---------- 持久化（mrp.storage：原子写 + 索引 + 线程池） ----------

    def _load_all(self) -> None:
        """启动加载角色/世界书（同步变体：构造期无事件循环）。坏条目隔离并告警。"""
        self.character_registry.load_all_sync()
        self.lorebook_registry.load_all_sync()
        self.world_registry.load_all_sync()
        for reg in (self.character_registry, self.lorebook_registry, self.world_registry):
            if reg.quarantine:
                logger.warning(
                    "%s 启动加载完成，%d 条损坏已隔离（详见上方日志）",
                    reg.kind,
                    len(reg.quarantine),
                )

    async def save_character(self, c: Character) -> None:
        await self.character_registry.save(c)

    async def save_lorebook(self, b: Lorebook) -> None:
        await self.lorebook_registry.save(b)

    async def persist_owned_branch_commit(self, staged) -> None:
        """SQL-only child persistence while BranchCommit retains its story gate."""
        if not hasattr(staged, 'expected_revision'):
            raise TypeError('Owned persistence requires a BranchCommit adapter')
        return await self._persist_session_once(staged)

    async def persist_session(self, runner: SessionRunner) -> None:
        # Checkpoints may already own the turn lock. SQL lifecycle and revision
        # fences avoid acquiring a story gate in the reverse lock order.
        return await self._persist_session_once(runner)

    async def _persist_session_once(self, runner: SessionRunner) -> None:
        watermark = self.memory_store.current_watermark(runner.state.meta.id)
        if hasattr(runner, "expected_revision") and hasattr(self.sessions, "commit_state"):
            await self.sessions.commit_state(runner.state, watermark,
                expected_revision=runner.expected_revision, operation_id=runner.commit_operation_id,
                outbox_events=[{"event": event, "data": payload} for event, payload, _lossy in runner.commit_events],
                memory_actions=runner.memory_actions,
                command_receipt=getattr(runner, "command_receipt", None))
        else:
            await self.sessions.save_state(runner.state, watermark, expected_revision=runner.state.meta.branch_revision)
        if isinstance(runner, SessionRunner) and not hasattr(runner, "_branch_committed_state"):
            runner._committed_state = runner.state.model_copy(deep=True)
        if getattr(self.sessions, "creation_active", lambda: False)():
            return  # Staged creation must not leak into derived search/backups.
        for name, schedule in (
            ("backup", lambda: self.story_backups.schedule(runner.state.meta.story_id or runner.state.meta.id)),
            ("search", lambda: self.story_search.schedule(runner.state)),
        ):
            try:
                schedule()
            except Exception:
                logger.warning("Story committed; derived %s scheduling deferred", name, exc_info=True)

    async def dispatch_story_outbox(self, *, runner=None, revision=None):
        """Replay durable notifications; delivery is idempotent via branch revision."""
        rows = await asyncio.to_thread(self.sessions.story_db.pending_events, 10000)
        delivered = 0
        for row in rows:
            if runner is not None and (row["branch_id"] != runner.state.meta.id or
                    (revision is not None and row["revision"] != revision)):
                continue
            event = row["event"]
            payload = {**event["data"], "branch_revision": row["revision"], "outbox_event_id": row["id"]}
            try:
                if runner is None:
                    await self.bus.publish(row["branch_id"], event["event"], payload)
                else:
                    await runner._emit(event["event"], payload)
                await asyncio.to_thread(self.sessions.story_db.acknowledge_event, row["id"])
                delivered += 1
            except Exception:
                logger.warning("durable story notification remains pending", exc_info=True)
                break
        return delivered

    async def load_session(self, session_id: str) -> SessionRunner | None:
        if await asyncio.to_thread(self.sessions.story_db.owns,session_id) and not await asyncio.to_thread(self.sessions.story_db.active,session_id):
            return None
        # Cancellation controls must reach a busy runtime without waiting behind
        # its model call's story gate. Cache identity remains authoritative here.
        cached = self.runners.get(session_id)
        if cached is not None and cached.busy():
            self.sessions.require_visible_branch(session_id)
            self.branch_runtimes.pin(session_id)
            return cached
        async with self.story_lifecycle.branch_gate(session_id):
            async with self.branch_runtimes.gate(session_id):
                runner = await self._load_session_once(session_id)
                if runner is not None:
                    self.branch_runtimes.pin(session_id)
                return runner

    async def _load_session_once(self, session_id: str) -> SessionRunner | None:
        if session_id in self.runners:
            self.runners.move_to_end(session_id)
            return self.runners[session_id]
        state = await self.sessions.load_state(session_id)  # 缺失/损坏 → None（含 v1→v2 迁移）
        if state is None:
            return None
        await asyncio.to_thread(self.memory_store.recover_generation_operations, state)
        runner = self._make_runner(state)
        if runner._conversation_recovered or runner._turn_recovered:
            await self.persist_session(runner)
        await self._register_runner(runner)
        return runner

    async def session_summaries(self) -> list[dict[str, Any]]:
        """会话摘要列表（B3：走索引，不再为每个会话建 runner / 解析会话体）。"""
        return [s.api_dict() for s in await self.sessions.list_summaries()]

    def _make_runner(self, state: SessionState) -> SessionRunner:
        from mrp.shared.player_identity import ensure_player_identity, current_identity
        from mrp.storage.story_media import capture_avatar
        ensure_player_identity(state, self.characters.get(state.meta.player_character_id or ""))
        identity = current_identity(state)
        if identity is not None and not identity.media_captured:
            if identity.avatar_ref is None and identity.character is not None:
                identity.avatar_ref = capture_avatar(self.data_root, identity.source_character_id)
            identity.media_captured = True
        for character in state.characters:
            self._apply_global_llm_settings(character)
        # Freeze legacy global-book bindings into this branch before new messages
        # receive state revisions. Later edits to the global library cannot alter
        # a historical fork point or another branch's prompt.
        if not state.lorebooks and state.meta.lorebook_ids:
            state.lorebooks = [
                self.lorebooks[bid].model_copy(deep=True)
                for bid in state.meta.lorebook_ids if bid in self.lorebooks
            ]
        books = state.lorebooks
        receipts, damaged = self.usage_ledger.read(state.meta.id)
        merged = {row.id: row for row in state.usage_records}
        # The append-only ledger also carries later label updates for a receipt.
        # A previously saved snapshot must not discard that latest revision.
        merged.update({row.id: row for row in receipts})
        state.usage_records = list(merged.values())
        state.usage_incomplete = state.usage_incomplete or damaged
        runner = SessionRunner(
            state,
            self.engine_manager,
            lorebooks=books,
            memory_store=self.memory_store,
            sink=self.bus,
            hygiene_judge=self.hygiene_judge,
            director_judge=self.director_judge,
            transition_llm=self.transition_llm,
            episodic_consolidator=self.episodic_consolidator,
            scene_summarizer=self.scene_summarizer,
            padding_gen=self.padding_gen,
            assist_generator=self.assist_generator,
            writing_generator=self.writing_generator,
            group_responder=self.group_responder,
            app_settings=self.settings,
            world_resolver=lambda world_id: self.worlds.get(world_id),
            prompt_preset_resolver=self.prompt_presets.get,
        )
        runner.writing_request_archive = self.request_archive
        runner.memory_command_port = self.memory_commands
        self.conversation_runs.attach(runner)
        self.turn_runs.attach(runner)
        runner.usage_recorder = self.usage_ledger.append
        return runner

    async def _register_runner(self, runner: SessionRunner, *, replace=False) -> None:
        async with self.story_lifecycle.branch_gate(runner.state.meta.id):
            if self.sessions.story_db.owns(runner.state.meta.id):
                self.sessions.require_visible_branch(runner.state.meta.id)
            return await self._register_runner_once(runner, replace=replace)

    async def _register_runner_once(self, runner: SessionRunner, *, replace=False) -> None:
        """登记 runner 并按 LRU 上限淘汰（B3）。

        只淘汰**空闲** runner：淘汰一个正在回合中的 runner，会让后续请求从磁盘
        重建出"陈旧影子 runner"，回合结束落盘后缓存里的状态反而不一致。
        因此全部忙时允许暂时超限（下次注册/回合结束即可回收）。
        """
        sid = runner.state.meta.id
        runner.memory_command_port = self.memory_commands
        existing = self.runners.get(sid)
        if existing is not None and existing is not runner and not replace:
            raise RuntimeError("A different runtime already owns this branch")
        self.branch_runtimes.pin(sid)
        if not hasattr(runner, "_committed_state"):
            runner._committed_state = runner.state.model_copy(deep=True)
        self.runners[sid] = runner
        self.runners.move_to_end(sid)
        while len(self.runners) > self.runner_cache:
            # 跳过刚登记的 runner 本身（调用方马上要用它）与忙碌中的 runner
            victim = next(
                (k for k, r in self.runners.items() if k != sid and not r.busy() and not self.branch_runtimes.leased(k)), None
            )
            if victim is None:
                logger.warning(
                    "运行器缓存超限但全部在回合中，暂不淘汰（%d/%d）",
                    len(self.runners),
                    self.runner_cache,
                )
                break
            evicted = self.runners.pop(victim)
            await evicted.aclose()  # W3：幂等（取消后台固化任务/清检查器）
            logger.info("运行器缓存淘汰: %s（上限 %d）", victim, self.runner_cache)

    async def drop_runner(self, session_id: str) -> None:
        """显式移除 runner（DELETE /sessions/{id}）：先关 runner 再摘除。"""
        async with self.branch_runtimes.gate(session_id):
            runner = self.runners.pop(session_id, None)
            if runner is not None:
                await runner.aclose()

    async def create_session(
        self, title: str, character_ids: list[str], player_persona: str, lorebook_ids: list[str],
        world_id: str | None = None, player_character_id: str | None = None,
        reply_max_tokens: int | None = None,
        *, opening_scene: str = "", greeting_choices: dict[str, int] | None = None, register: bool = True,
    ) -> SessionRunner:
        chars = [self.characters[cid].model_copy(deep=True)
                 for cid in character_ids if cid in self.characters]
        if not chars:
            raise ValueError("会话至少需要一个已导入角色")
        by_id = {character.id: character for character in chars}
        for character_id, choice in (greeting_choices or {}).items():
            character = by_id.get(character_id)
            if character is None:
                raise ValueError("开场白只能为本次参与角色选择")
            greetings = [character.card.first_mes, *character.card.alternate_greetings]
            if not isinstance(choice, int) or choice < 0 or choice >= len(greetings):
                raise ValueError("所选备用开场白已不存在，请重新选择")
            character.card.first_mes = greetings[choice]
        if player_character_id:
            player_character = self.characters.get(player_character_id)
            if player_character is None:
                raise ValueError("所选玩家角色卡不存在")
            if player_character_id in character_ids:
                raise ValueError("玩家角色卡不能同时作为参与角色")
            if not player_persona.strip():
                from mrp.shared.prompt import player_persona_from_card
                player_persona = player_persona_from_card(player_character.card)
        world = self.worlds.get(world_id) if world_id else None
        if world_id and world is None:
            raise ValueError("所选世界不存在")
        if world is not None and world.archived:
            raise ValueError("归档世界不能用于新故事")
        for character in chars:
            self._apply_global_llm_settings(character)
        # R35 v2：新会话恒有初始场景（全角色在场）
        initial_scene = Scene(
            title="开场",
            member_ids=[c.id for c in chars],
        )
        state = SessionState(
            schema_version=3,
            meta=SessionMeta(
                title=title or f"与{chars[0].card.name}的会话",
                player_persona=player_persona,
                player_character_id=player_character_id,
                reply_max_tokens=reply_max_tokens,
                character_ids=[c.id for c in chars],
                lorebook_ids=lorebook_ids,
                source_world_id=world.id if world else None,
                source_world_revision=world.revision if world else None,
                world_core_brief=world.core_brief if world else "",
                world_runtime_policy=getattr(world, 'runtime_policy', 'legacy_full') if world else 'legacy_full',
                world_archive_records=(
                    [record.model_dump(mode="json") for record in world.archive_records]
                    if world else []
                ),
            ),
            characters=chars,
            scenes=[initial_scene],
            active_scene_id=initial_scene.id,
        )
        self._apply_opening_scene(state, opening_scene)
        runner = self._make_runner(state)
        if register:
            await self._register_runner(runner)
        return runner

    @staticmethod
    def _apply_opening_scene(state: SessionState, opening_scene: str) -> None:
        """A story-specific opening replaces preset narration and card greetings.

        Keep the event in visible history and the active scene description so
        later turns still know the current situation after history compression.
        """
        opening = opening_scene.strip()
        if not opening:
            return
        scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
        if scene is None:
            raise ValueError("开场场景不存在")
        state.messages = [
            message for message in state.messages
            if not (message.turn == 0 and message.actor == "director" and message.kind == "scene")
        ]
        scene.title = "开场"
        scene.description = opening
        state.messages.append(Message(
            session_id=state.meta.id,
            seq=state.next_seq(),
            turn=0,
            actor="director",
            content=opening,
            kind="scene",
            visible_to="all",
            scene_id=scene.id,
        ))

    async def create_scenario_session(self, package: ScenarioPackage, *, opening_scene: str = "", register: bool = True) -> SessionRunner:
        """从可移植预设创建隔离的角色/世界书快照。"""
        state = instantiate_scenario(package)
        self._apply_opening_scene(state, opening_scene)
        for character in state.characters:
            self._apply_global_llm_settings(character)
        runner = self._make_runner(state)
        if register:
            await self._register_runner(runner)
        return runner

    async def restore_runner(self, state: SessionState) -> SessionRunner:
        """R6.5 存档恢复：建 runner → 登记缓存 → 落盘会话。"""
        sid = state.meta.id
        async with self.branch_runtimes.gate(sid):
            previous = self.runners.get(sid)
            if previous is not None and previous.busy():
                from mrp.application.branch_commit import BranchCommitConflict
                raise BranchCommitConflict("路线正在生成，请先停止后再恢复存档")
            from contextlib import nullcontext
            async with (previous.runtime.turn_lock if previous is not None else nullcontext()):
                runner = self._make_runner(state.model_copy(deep=True))
                current = await self.sessions.load_state(sid)
                if current is not None:
                    runner.state.meta.branch_revision = current.meta.branch_revision
                else:
                    # A copied state under a new identity begins at revision 0;
                    # its source revision is not a CAS token for an absent row.
                    runner.state.meta.branch_revision = 0
                await self.persist_session(runner)
                if previous is not None:
                    await previous.aclose()
                await self._register_runner(runner, replace=True)
                return runner

    def busy_any(self) -> bool:
        return any(r.busy() for r in self.runners.values())

    async def aclose(self) -> None:
        """释放容器资源：取消预热任务 → 关闭 TTS → 关 runner/引擎/SQLite。"""
        await self.conversation_runs.close()
        for task in list(self.story_backups._scheduled.values()):
            task.cancel()
        self.story_backups._scheduled.clear()
        for task in list(self.story_search._scheduled.values()):
            task.cancel()
        self.story_search._scheduled.clear()
        for t in list(self._warm_tasks):
            t.cancel()
        self._warm_tasks.clear()
        try:
            for runner in list(self.runners.values()):
                await runner.aclose()  # W3：幂等；关停路径不在回合中
            self.runners.clear()
        finally:
            # 即使一个子系统关闭出错，TTS、引擎与 SQLite 仍逐项释放。
            try:
                await self.tts_manager.stop()
            finally:
                try:
                    await self.engine_manager.shutdown_all()
                finally:
                    try:
                        await self.world_organize.close()
                        await self.asset_imports.close()
                    finally:
                        try:
                            await self.lorebook_generation.close()
                        finally:
                            try:
                                await self.card_inspiration.close()
                            finally:
                                self.memory_store.close()  # W5：幂等（WAL close 前显式 commit）
                                logger.info("AppContainer 已关闭: %s", self.data_root)

    def spawn_tts_if_enabled(self) -> None:
        """Load the local speech runtime after app startup only when the user enabled it."""
        if not self.settings.tts.enabled:
            return
        try:
            task = asyncio.get_running_loop().create_task(self.tts_manager.start_background())
            self._warm_tasks.add(task)
            task.add_done_callback(self._warm_tasks.discard)
        except RuntimeError:
            return

    # ---------- R28.2 引擎预热 ----------

    def _track_warm_task(self, coro) -> None:
        """把预热协程挂到后台（幂等、静默；无事件循环时跳过）。"""
        from mrp.server.warmup import warmup_enabled

        if not warmup_enabled():
            coro.close()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)

    def spawn_warm(self, characters: list[Character]) -> None:
        """后台预热给定角色（native/fake 引擎≈空操作）。"""
        from mrp.server.warmup import warm_characters

        if characters:
            self._track_warm_task(warm_characters(self.engine_manager, characters))

    def spawn_warm_recent(self) -> None:
        """后台预热"最近一个有过消息的会话"（服务启动 / 设置变更后调用）。"""
        self._track_warm_task(self.warm_recent_session())

    async def warm_recent_session(self) -> list[str]:
        """预热最近会话的在场角色；返回就绪角色 id。"""
        from mrp.server.warmup import warm_characters, warmup_enabled

        if not warmup_enabled():
            return []
        for summary in await self.sessions.list_summaries():  # 已按 updated_at 倒序
            if summary.messages <= 0:
                continue
            r = await self.load_session(summary.id)
            if r and r.state.messages:
                return await warm_characters(self.engine_manager, r.state.characters)
        return []

    def warming(self) -> bool:
        return any(not t.done() for t in self._warm_tasks)


def build_container_from_env() -> AppContainer:
    """工厂：读 env 组装容器（生产路径唯一入口；行为与旧址模块级 World() 一致）。"""
    data_root = validate_data_root(default_data_root(), PROJECT_ROOT)
    web_dist_env = os.environ.get("MRP_WEB_DIST", "")
    web_dist = Path(web_dist_env) if web_dist_env else DEFAULT_WEB_DIST
    return AppContainer(
        data_root=data_root,
        settings=load_settings(data_root),
        config=AppContainerConfig.from_env(),
        web_dist=web_dist,
    )
