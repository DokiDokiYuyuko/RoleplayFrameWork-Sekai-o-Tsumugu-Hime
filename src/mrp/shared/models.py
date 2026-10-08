"""mrp 核心数据模型（可迁移标准，ADR-0002 约束）。

设计原则：
- 全部 Pydantic v2 模型，schema_version 显式携带，JSON 可完整序列化/往返
- 字段语义与需求 v1.0 的 R 编号对应（注释标注）
- 跨模块边界的模型集中于此：编排器 / 引擎 / 导入器 / API 共用
- 模型保持"数据+纯读"，状态变更逻辑在 orchestrator
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SchemaVersion = Literal[1, 2, 3]  # v2 场景；v3 世界线身份与状态修订
ActorId = str  # "player" 或角色 id
Visibility = list[ActorId] | Literal["all"]  # R3.1 两档
InsertAnchor = Literal["system", "at_depth", "near"]  # ST position 0..7 的三档归并


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def fingerprint(actor: ActorId, seq: int, content: str) -> str:
    """内容指纹——防误写锚点（R5.3）：swipe 检测与记忆写入门都靠它。"""
    h = hashlib.sha256()
    h.update(actor.encode("utf-8"))
    h.update(str(seq).encode("ascii"))
    h.update(content.encode("utf-8"))
    return h.hexdigest()[:16]


# ---------- 生成元数据 ----------


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("usage"))
    purpose: str = "generation"
    model: str = ""
    character_id: str | None = None
    generation_id: str | None = None
    operation_id: str | None = None
    attempt_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    created_at: datetime = Field(default_factory=utcnow)


# ---------- 输出卫生（R34 数据结构，R32 阶段先落骨架） ----------


class Violation(BaseModel):
    """一次校验违规（R34.1）：类别 + 回复原文中的证据句。"""

    category: Literal["C1", "C2", "C3", "C4"]  # 抢戏玩家/抢戏他角/心灵感应/出戏
    evidence: str  # 逐字引用回复中的句子


class HygieneReport(BaseModel):
    """一条（候选）回复的输出卫生校验报告（R34）。"""

    passed: bool = True
    attempts: int = 1  # 1=一次过；2=重试后
    corrected: bool = False  # 重试后被修正
    violations: list[Violation] = Field(default_factory=list)  # 最终仍存在的违规
    usage: TokenUsage = Field(default_factory=TokenUsage)  # judge 模型开销（R34.4）


class MessageSourceRef(BaseModel):
    """The exact active text a generation or scheduling decision consumed."""

    message_id: str
    variant_id: str | None = None
    fingerprint: str


class GenerationProvenance(BaseModel):
    run_id: str | None = None
    step_id: str | None = None
    mode: Literal["single", "serial", "parallel", "free"] = "single"
    participants: list[str] = Field(default_factory=list)
    trigger_message_ids: list[str] = Field(default_factory=list)
    reply_to_message_ids: list[str] = Field(default_factory=list)
    sources: list[MessageSourceRef] = Field(default_factory=list)
    scheduling_sources: list[MessageSourceRef] = Field(default_factory=list)
    baseline_message_ids: list[str] = Field(default_factory=list)
    baseline_state: dict[str, Any] = Field(default_factory=dict)
    baseline_state_revision_id: str | None = None
    baseline_branch_revision: int = 0
    baseline_memory_watermark: int | None = None
    shared_scene: str = ""
    shared_frame: str = ""
    director_directive: str = ""
    complete: bool = False


class GenerationMeta(BaseModel):
    generation_id: str | None = None
    operation_id: str | None = None
    attempt_id: str | None = None
    message_id: str | None = None
    request_ids: list[str] = Field(default_factory=list)
    plan_id: str | None = None
    provenance: GenerationProvenance | None = None
    response_style: dict | None = None
    memory_recall: list[dict] = Field(default_factory=list)
    """一次模型生成的审计数据（注入检查器数据源，R6.4；成本统计源，非功能 1）。"""

    engine_session_id: str = ""
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    completion_state: Literal["complete", "truncated", "filtered", "unknown"] = "unknown"
    injected_entry_ids: list[str] = Field(default_factory=list)  # 世界书+记忆条目溯源
    prompt_tokens_by_section: dict[str, int] = Field(default_factory=dict)


# ---------- 消息 ----------


class MessageVariant(BaseModel):
    """一次生成候选的完整快照（R32.1 swipe 历史；不参与任何 prompt 组装）。"""

    id: str = Field(default_factory=lambda: new_id("var"))
    content: str
    generation_meta: GenerationMeta | None = None  # 每变体独立审计
    hygiene: HygieneReport | None = None  # 该变体生成时的校验报告（R34）
    created_at: datetime = Field(default_factory=utcnow)
    accepted_dependency_sources: list[MessageSourceRef] = Field(default_factory=list)


class Message(BaseModel):
    """世界消息日志的一条记录。世界真相以 Message 流为准（R3.1）。"""

    id: str = Field(default_factory=lambda: new_id("msg"))
    session_id: str
    seq: int  # 会话内单调递增
    turn: int  # 回合号：一次玩家输入触发的所有消息同 turn
    actor: ActorId
    content: str
    kind: Literal["roleplay", "scene", "ooc", "system_event", "inner"] = "roleplay"
    visible_to: Visibility = "all"
    status: Literal["pending", "final", "retracted"] = "final"
    fingerprint: str = ""
    generation_meta: GenerationMeta | None = None
    created_at: datetime = Field(default_factory=utcnow)
    # ---- R32 消息操控（additive；content 永远是 prompt 唯一来源）----
    variants: list[MessageVariant] = Field(default_factory=list)
    active_variant: int | None = None  # 指向 variants 索引；None=从未 swipe
    edited: bool = False  # 玩家手动编辑过（审计）
    hygiene: HygieneReport | None = None  # 镜像 active variant 的报告（展示用）
    # ---- R35 场景归属（additive；v1 迁移时回填初始场景 id）----
    scene_id: str | None = None
    # ---- R45 重跑最后一轮（additive）：R30 显式 @ 路由随消息持久化，重跑时复用 ----
    mentions: list[str] = Field(default_factory=list)
    # Segments parsed from one player submission share this stable edit-group id.
    # Legacy messages omit it and are grouped by adjacent player messages in one turn.
    input_group_id: str | None = None
    # Multi-actor coordination (additive; omitted on legacy messages).
    reply_mode: Literal["auto", "parallel", "serial", "free"] | None = None
    executed_reply_mode: Literal["parallel", "serial", "free"] | None = None
    reply_order: list[str] = Field(default_factory=list)
    reply_reason: str = ""
    reply_basis_fingerprint: str = ""
    reply_shared_scene: str = ""
    idempotency_key: str | None = None  # 会话内幂等写入（群体立即回应）
    # v3：本消息完成后生效的剧情状态修订；旧消息缺失时不能据此还原历史。
    post_state_revision_id: str | None = None
    # Control and story identity are separate from the legacy transport actor.
    player_identity_id: str | None = None
    person_id: str | None = None
    known_to: list[str] | None = None
    control_event: bool = False
    generation_id: str | None = None
    operation_id: str | None = None
    attempt_id: str | None = None
    dependency_stale: bool = False
    dependency_stale_sources: list[str] = Field(default_factory=list)
    accepted_dependency_sources: list[MessageSourceRef] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.fingerprint:
            self.fingerprint = fingerprint(self.actor, self.seq, self.content)

    def can_see(self, actor: ActorId) -> bool:
        """角色上下文可见性裁决（R3.1/R3.2）。

        retracted 消息对角色不可见（等价于从未发生）——玩家 UI 仍可
        划线显示，那是前端的事。
        """
        if self.status == "retracted":
            return False
        if actor != "player" and self.known_to is not None:
            if actor not in self.known_to:
                return False
            if self.kind == "inner" and self.person_id == actor:
                return True
            return self.visible_to == "all" or actor in self.visible_to or "player" in self.visible_to
        if self.visible_to == "all":
            return True
        return actor in self.visible_to


# ---------- 角色 ----------


class CharacterCard(BaseModel):
    """归一化角色卡：CCv2 为主，CCv3 按 V2 超集收（R1.1，导入器负责归一）。"""

    model_config = ConfigDict(extra="allow")

    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    name: str
    description: str = ""
    appearance: str = ""  # 本项目扩展：身体、外貌与稳定形态；旧卡缺字段时默认为空
    traits_label: str = "能力与实力"  # 可按题材重命名；非超凡题材可用技能、专长或其他核心特质
    traits: str = ""  # 角色能力、实力、技能、专长或用户自定义的核心特质
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    system_prompt: str | None = None  # 卡内覆盖（{{original}} 占位语义在导入器处理）
    post_history_instructions: str | None = None
    creator_notes: str = ""
    creator: str = ""
    character_version: str = ""
    tags: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
    avatar_path: str | None = None
    source_format: str = "ccv2"  # ccv1/ccv2/ccv3/charx


class ModelConfig(BaseModel):
    """每角色独立模型配置（R1.3）。进程级单选——ADR-0001 每角色一进程的依据。"""

    model_config = ConfigDict(extra="allow")

    provider: str = "deepseek-official"
    model: str = "deepseek/deepseek-v4-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"  # 只存环境变量名，绝不存密钥
    inherit_model: bool | None = None
    inherit_base_url: bool | None = None
    effective_provider: str = ""
    sampling: dict[str, Any] = Field(default_factory=dict)  # max_tokens/reasoning_effort 等


class CharacterAuthoringSource(BaseModel):
    """Retained author manuscript, never part of a character persona."""

    text: str
    captured_at: datetime = Field(default_factory=utcnow)
    import_job_id: str | None = None


class Character(BaseModel):
    """角色 = 卡（不变人设）+ 运行时状态（R1.2 分离原则）。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: new_id("char"))
    schema_version: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    source_asset_id: str | None = None
    source_asset_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    card: CharacterCard
    source_world_id: str | None = None  # Organization only; never automatically loads a world.
    authoring_source: CharacterAuthoringSource | None = None
    aliases: list[str] = Field(default_factory=list)  # 提及检测（R2.4）
    bound_lorebook_ids: list[str] = Field(default_factory=list)  # ADR-0004：内嵌书提取后的绑定关系
    llm: ModelConfig = Field(default_factory=ModelConfig)
    talkativeness: float = 0.5  # 0=Shy 1=Chatty（对齐 ST 语义）
    muted: bool = False  # 导演排除（R2.2）
    present: bool = True  # 在场标记（R3.2）
    # R38 主动性开关（默认被动：不@不说话；玩家掌控节奏与成本）
    interject_enabled: bool = False  # 主动插话：未被提及也可被导演选入本轮
    followup_enabled: bool = False  # 接话：他人发言后可链式反应（链深≤2）

    @property
    def mention_names(self) -> list[str]:
        """提及检测用的名字集合：卡名 + 别名（去空白去重）。"""
        seen: dict[str, None] = {}
        for n in [self.card.name, *self.aliases]:
            n = n.strip()
            if n:
                seen.setdefault(n, None)
        return list(seen)


# ---------- 世界书 ----------

SelectiveLogic = Literal[0, 1, 2, 3]  # ST 语义：AND_ANY/NOT_ALL/NOT_ANY/AND_ALL


class LorebookEntry(BaseModel):
    """统一世界书条目（无损承载 ST 为主，R4.1；缺字段全默认值容错）。"""

    model_config = ConfigDict(extra="allow")

    uid: int
    keys: list[str] = Field(default_factory=list)  # 支持 '/.../' 正则串（Risu 风格）
    secondary_keys: list[str] = Field(default_factory=list)
    content: str = ""
    comment: str = ""
    enabled: bool = True
    constant: bool = False  # 蓝灯常驻
    selective: bool = False
    selective_logic: SelectiveLogic = 0
    order: int = 100  # ST 语义：越大越靠近上下文末尾（影响越大）
    anchor: InsertAnchor = "system"  # 三档插入锚（ST position 0..7 由导入器映射）
    depth: int = 4  # anchor=at_depth 时
    probability: int = 100  # 0-100，按 rng 决定（种子入决策日志）
    extensions: dict[str, Any] = Field(default_factory=dict)  # ST 专有字段（sticky/cooldown/递归开关等）


class Lorebook(BaseModel):
    """一本世界书。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: new_id("book"))
    schema_version: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    source_asset_id: str | None = None
    source_asset_revision: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    name: str = "未命名世界书"
    description: str = ""
    tags: list[str] = Field(default_factory=list, max_length=30)
    entries: list[LorebookEntry] = Field(default_factory=list)
    scan_depth: int = 2  # 扫描最近 N 回合的可见消息（R4.1）
    token_budget: int = 1024
    recursive_scanning: bool = True  # 一级递归（v1）
    source_format: str = "st"  # st/risu/embedded


# ---------- 注入与提示组装 ----------


class Injection(BaseModel):
    """一次注入（世界书条目或记忆片段）——PromptComposer 的原料。"""

    source: Literal["lorebook", "memory", "system", "archive"]
    entry_id: str  # 溯源 id（进 generation_meta.injected_entry_ids）
    content: str
    anchor: InsertAnchor = "system"
    depth: int = 4
    order: int = 100
    tokens: int = 0  # 由 PromptComposer 估算填充
    reason: str = ""  # 触发原因（注入检查器展示）
    placement: Literal["background", "current", "instructions"] = "background"


class ComposedPrompt(BaseModel):
    """PromptComposer 输出：正文 + 分节 token 统计（R6.4 数据源）。"""

    text: str
    tokens_by_section: dict[str, int] = Field(default_factory=dict)
    total_tokens: int = 0
    included_entry_ids: list[str] = Field(default_factory=list)
    included_message_ids: list[str] = Field(default_factory=list)
    omitted_reasons: dict[str, str] = Field(default_factory=dict)
    entry_sections: dict[str, str] = Field(default_factory=dict)
    message_sections: dict[str, str] = Field(default_factory=dict)


# ---------- 记忆 ----------


class MemoryRecord(BaseModel):
    """角色记忆（R5.2）：明文可读可编辑；只收 final 消息（R5.3 防误写）。
    R36 additive：情景/场景摘要两 kind + 结构化字段（检索与查看器用）。"""

    id: str = Field(default_factory=lambda: new_id("mem"))
    character_id: str
    session_id: str = ""
    turn_start: int = 0
    turn_end: int = 0
    kind: Literal["summary", "fact", "manual", "episodic", "scene"] = "summary"
    content: str
    source_message_ids: list[str] = Field(default_factory=list)
    # v3: branch-local commit order and the last message required by this fact.
    # Empty effective_message_id means a manual/unanchored record; it is not
    # inherited at a historic fork unless the user gives it an explicit anchor.
    effective_message_id: str | None = None
    commit_seq: int = Field(default=0, ge=0)
    inherited_from_id: str | None = None
    invalidated: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    # ---- R36 结构化（additive；旧记录默认值容错）----
    participants: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: int = 3  # 1=寒暄 3=普通互动 5=重大事件/关系转折
    scene_id: str = ""   # kind=scene 时必填（场景摘要边界）
    category: Literal["experience", "relationship", "unfinished"] = "experience"
    participant_ids: list[str] = Field(default_factory=list)
    source_fingerprints: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, str] = Field(default_factory=dict)
    important: bool = False
    revision: int = Field(default=1, ge=1)
    manually_revised: bool = False
    source_changed: bool = False
    revisions: list[dict] = Field(default_factory=list)
    matter_status: Literal["open", "completed", "cancelled", "unknown"] = "unknown"
    supersedes: list[str] = Field(default_factory=list)


class PinnedFact(BaseModel):
    """玩家在本局明确固定的剧情事实；与可检索的长期记忆分开保存。"""

    id: str = Field(default_factory=lambda: new_id("pin"))
    content: str = Field(min_length=1, max_length=10000)
    source_message_id: str | None = None
    source_actor: ActorId | None = None
    visible_to: Visibility = "all"
    created_at: datetime = Field(default_factory=utcnow)


# ---------- 导演 ----------


class ScoredCandidate(BaseModel):
    character_id: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class DirectorDecision(BaseModel):
    """导演决策（R2.2）：为什么选他——可复现（rng_seed）可审计。
    R35 v2：action 三态 + rationale + scene 载荷（trigger 枚举 additive 扩展）。"""

    turn: int
    trigger: Literal[
        "mention", "talkativeness", "manual_force", "open_round", "explicit_mention",
        "llm_route",        # LLM 导演选角（替代裸轮盘槽位）
        "llm_switch_scene",  # LLM 导演判场景切换
        "llm_end_scene",     # LLM 导演判话题收束
        "player_scene",      # 玩家手动/旁白触发的切换（API 直切）
    ]
    action: Literal["pick_speaker", "switch_scene", "end_scene"] = "pick_speaker"
    candidates: list[ScoredCandidate] = Field(default_factory=list)
    chosen: list[ActorId] = Field(default_factory=list)  # 可多人排队
    rationale: str = ""  # LLM 决策理由（进决策日志 + DirectorBanner）
    scene: SceneAction | None = None  # action=switch_scene/end_scene 时的载荷
    rng_seed: int = 0
    created_at: datetime = Field(default_factory=utcnow)


# ---------- 场景（R35） ----------


class Scene(BaseModel):
    """一段连续的叙事场景（地点/时间/在场快照）。

    不变式：v2 会话恒有 active scene（end_scene 也开后继场景）。
    scene_id 不参与 prompt 过滤——"楼下听不见"由 visible_to 机器保证
    （场景切换=批量 presence 变更，design/v3/m9-r35 §5）。scene_id 只承担
    UI 分隔线 / R36.3 摘要边界 / 审计归属三职。
    """

    id: str = Field(default_factory=lambda: new_id("scene"))
    title: str = ""  # 地点，如"天台"；初始场景="开场"
    description: str = ""  # 时间/氛围一句话
    builtin_image_id: str | None = None  # Explicit public artwork selection; not narrative material.
    turn_start: int = 0
    turn_end: int | None = None  # None=进行中
    member_ids: list[str] = Field(default_factory=list)  # 在场角色快照（不含 player）
    group_ids: list[str] = Field(default_factory=list)  # 当前场景中的群体参与者
    created_at: datetime = Field(default_factory=utcnow)


class GroupActor(BaseModel):
    """会话内的群体参与者；不是独立角色卡或独立引擎。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: new_id("group"))
    label: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    count: int | None = Field(default=None, ge=0, le=1000000)
    scene_id: str
    joined_seq: int = Field(ge=0)
    status: Literal["active", "left"] = "active"
    public_brief: str = Field("", max_length=4000)
    current_state: str = Field("", max_length=2000)
    director_note: str = Field("", max_length=4000)
    participation: Literal["on_cue", "occasional"] = "on_cue"
    last_spoke_turn: int | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    source_snapshot: str = Field("", max_length=8000)
    creation_key: str | None = None
    creation_request_hash: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StateRevision(BaseModel):
    """世界线某个锚点的状态快照；消息正文由 SessionState.messages 单独保存。"""

    id: str = Field(default_factory=lambda: new_id("revision"))
    after_message_id: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    # Canonical JSON SHA-256; older v3 revisions may omit it and are compared by snapshot.
    content_hash: str = ""
    # None = v2 旧数据尚无可靠的记忆提交水位，不能据此分叉历史记忆。
    memory_watermark: int | None = None
    source: Literal["legacy_head", "head_only", "runtime"] = "runtime"
    created_at: datetime = Field(default_factory=utcnow)


class StoryEvent(BaseModel):
    """玩家标记的剧情事件；默认只供浏览，不自动注入模型。"""

    id: str = Field(default_factory=lambda: new_id("event"))
    anchor_message_id: str
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=5000)
    kind: Literal["turning_point", "clue", "relationship", "scene", "note"] = "note"
    created_by: Literal["player", "director"] = "player"
    visible_to: Visibility = "all"
    created_at: datetime = Field(default_factory=utcnow)


class StoryBookmark(BaseModel):
    id: str = Field(default_factory=lambda: new_id("bookmark"))
    story_id: str
    branch_id: str
    message_id: str
    title: str = Field("", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_anchor: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class SceneAction(BaseModel):
    """switch_scene 决策载荷（LLM 导演输出或玩家手动指定）。"""

    title: str
    description: str = ""
    member_ids: list[str] = Field(default_factory=list)  # 新场景在场角色 id
    transition_hint: str = ""  # 过渡消息生成提示（"夜风、楼梯间"）


# ---------- 会话与存档 ----------


class PlayerIdentity(BaseModel):
    id: str = Field(default_factory=lambda: new_id("identity"))
    person_id: str
    source_character_id: str | None = None
    name: str = "玩家"
    persona: str = ""
    character: Character | None = None
    avatar_ref: str | None = None
    media_captured: bool = False
    start_seq: int = 0
    idempotency_key: str | None = None
    request_fingerprint: str = ""
    previous_disposition: Literal["npc", "leave"] | None = None


class ConversationStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    index: int = Field(ge=1)
    epoch: int = 0
    generation_id: str
    attempt_id: str | None = None
    speaker_id: str
    message_id: str | None = None
    status: Literal["committed", "failed", "cancelled"]
    trigger_message_ids: list[str] = Field(default_factory=list)
    reply_to_message_ids: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = None


class ConversationRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str
    operation_id: str
    request_fingerprint: str = ""
    mode: Literal["free", "observe"] = "observe"
    participant_ids: list[str] = Field(default_factory=list)
    scene_id: str
    player_identity_id: str | None = None
    directive: str = ""
    max_replies: int = Field(default=6, ge=1)
    completed_replies: int = Field(default=0, ge=0)
    status: Literal["queued", "running", "paused", "awaiting_user", "completed", "failed", "interrupted", "cancelled"] = "queued"
    stage: Literal["selecting", "generating", "committing", "boundary"] = "boundary"
    current_speaker_id: str | None = None
    stop_reason: str = ""
    last_error: str | None = None
    epoch: int = 0
    pause_requested: bool = False
    stop_requested: bool = False
    last_committed_step_id: str | None = None
    last_committed_message_id: str | None = None
    cumulative_usage: TokenUsage = Field(default_factory=TokenUsage)
    usage_incomplete: bool = False
    turn: int = 0
    seed_message_ids: list[str] = Field(default_factory=list)
    current_step_id: str | None = None
    current_message_id: str | None = None
    current_generation_id: str | None = None
    current_attempt_id: str | None = None
    current_trigger_message_ids: list[str] = Field(default_factory=list)
    current_reply_to_message_ids: list[str] = Field(default_factory=list)
    current_usage: TokenUsage = Field(default_factory=TokenUsage)
    scheduling_trace: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[ConversationStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TurnSlot(BaseModel):
    actor_id: str
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    seq: int
    generation_id: str = Field(default_factory=lambda: new_id("gen"))
    status: Literal["pending", "committed", "failed", "cancelled"] = "pending"
    error: str | None = None


class TurnRun(BaseModel):
    """Durable ordinary player submission; incomplete replies are never story messages."""
    id: str = Field(default_factory=lambda: new_id("turn"))
    session_id: str
    operation_id: str
    request_fingerprint: str
    request: dict[str, Any] = Field(default_factory=dict)
    player_identity_id: str | None = None
    scene_id: str | None = None
    turn: int = 0
    status: Literal["running", "awaiting_director", "completed", "failed", "interrupted", "cancelled"] = "running"
    epoch: int = 0
    input_message_ids: list[str] = Field(default_factory=list)
    slots: list[TurnSlot] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    parallel_context: dict[str, Any] | None = None
    parallel_memory_watermark: int | None = None
    followups: dict[str, str | None] = Field(default_factory=dict)
    prepared_scene_message_ids: list[str] = Field(default_factory=list)
    padding_prepared: bool = False
    basis_fingerprint: str = ""
    errors: list[str] = Field(default_factory=list)
    last_error: str | None = None
    stop_reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SessionMeta(BaseModel):
    response_style_id: str | None = None
    response_style_overrides: dict[str, str | None] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: new_id("sess"))
    title: str = ""
    # v3 世界线：根分支以自身 id 为 story_id；子分支显式记录来源。
    story_id: str = ""
    branch_name: str = ""
    parent_branch_id: str | None = None
    fork_message_id: str | None = None
    fork_state_revision_id: str | None = None
    fork_save_id: str | None = None
    fork_request_hash: str | None = None
    branch_revision: int = Field(default=0, ge=0)
    archived: bool = False
    player_persona: str = ""  # 玩家人设描述
    player_identity_id: str | None = None
    player_character_id: str | None = None  # 来源角色卡；人设正文保留为本局快照
    reply_max_tokens: int | None = None  # None=沿用角色卡；0=自动；正数=会话上限
    character_ids: list[str] = Field(default_factory=list)
    lorebook_ids: list[str] = Field(default_factory=list)
    options_enabled: bool = True  # R27：回合后选项生成开关
    options_style: str = "mixed"  # 行动向 action / 对话向 dialogue / 混合 mixed
    # M12-R41（D14 定案）：点击候选的行为——False=填入输入框待确认（默认）；True=直接发送
    options_direct_send: bool = False
    streaming_enabled: bool = True  # R33.4：流式输出开关（伪流式 delta 序列）
    hygiene_enabled: bool = True  # R34.2：输出卫生校验开关（默认开+自动重试一次）
    director_mode: Literal["auto", "confirm", "rules"] = "auto"  # R35.3：auto=LLM直接执行/confirm=人工确认/rules=纯v1规则
    # ---- R36 记忆分层（additive）----
    memory_enabled: bool = True
    memory_interval_turns: int = 5  # 常规固化间隔（回合）
    memory_top_k: int = 3  # 每回合检索注入条数
    memory_compress_horizon_turns: int = 15  # 超过此距离的已结束场景用摘要替代原文
    # ---- R37 叙事控制（additive，不动 schema）----
    narrative_pov: Literal["free", "second", "third"] = "free"
    narrative_density: Literal["dialogue", "balanced", "atmosphere"] = "balanced"
    short_input_padding: bool = True  # ≤4 字自动垫场
    # ---- R38 主动性（additive）----
    proactive_turn_limit: int = 1  # 每回合全局主动发言上限（0-3）
    scenario_instructions: str = ""  # 场景预设提供的剧情约定（会话快照）
    source_scenario_id: str | None = None  # 来源预设；只作追溯，不联动修改
    source_package_id: str | None = None  # 可跨设备识别的来源预设身份
    source_package_revision: int | None = None
    source_world_id: str | None = None  # 来源世界；只作追溯
    source_world_revision: int | None = None
    world_core_brief: str = ""  # 开局时固定的核心摘要快照
    world_archive_records: list[dict[str, Any]] = Field(default_factory=list)  # 世界档案快照；旧故事可补齐
    world_runtime_policy: Literal['legacy_full', 'raw', 'compiled'] = 'legacy_full'
    prompt_preset_id: str | None = None
    prompt_preset_snapshot: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def fill_worldline_defaults(self) -> "SessionMeta":
        if not self.story_id:
            self.story_id = self.id
        if not self.branch_name:
            self.branch_name = self.title
        return self


class SessionState(BaseModel):
    usage_records: list[UsageRecord] = Field(default_factory=list)
    usage_incomplete: bool = False
    """会话全量状态（内存态；持久化经 SaveFile，R6.5）。"""

    schema_version: SchemaVersion = 1
    meta: SessionMeta
    messages: list[Message] = Field(default_factory=list)
    conversation_runs: list[ConversationRun] = Field(default_factory=list)
    turn_runs: list[TurnRun] = Field(default_factory=list)
    pending_director: DirectorDecision | None = None
    pinned_facts: list[PinnedFact] = Field(default_factory=list, max_length=20)
    characters: list[Character] = Field(default_factory=list)
    # 新场景预设会话保存世界书快照；旧会话为空时仍按 meta.lorebook_ids 回退到资源库。
    lorebooks: list[Lorebook] = Field(default_factory=list)
    director_log: list[DirectorDecision] = Field(default_factory=list)
    # ---- R35 schema v2（迁移见 orchestrator/migration.py）----
    scenes: list[Scene] = Field(default_factory=list)
    active_scene_id: str | None = None
    # 会话内临时群体角色；旧故事缺失时按空列表读取。
    groups: list[GroupActor] = Field(default_factory=list)
    # 会话中途加入的角色只能读取入场后的消息；旧故事缺少键时按开局角色处理。
    character_joined_at_seq: dict[str, int] = Field(default_factory=dict)
    # 用户确认的入场上下文，仅注入对应角色的模型请求。
    character_entry_briefs: dict[str, str] = Field(default_factory=dict)
    # v3：修订只在真实状态变化时增加；旧历史不补造逐消息修订。
    state_revisions: list[StateRevision] = Field(default_factory=list)
    head_state_revision_id: str | None = None
    story_events: list[StoryEvent] = Field(default_factory=list)
    bookmarks: list[StoryBookmark] = Field(default_factory=list)
    player_identities: list[PlayerIdentity] = Field(default_factory=list)
    player_people: dict[str, Character] = Field(default_factory=dict)
    generation_operations: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # ---- 只读便捷方法（变更逻辑在 orchestrator/session.py）----

    def next_seq(self) -> int:
        # max-scan（R32.3）：删除最后一条后不复用已用序号（防 fingerprint 撞号）
        return max((m.seq for m in self.messages), default=-1) + 1

    def current_turn(self) -> int:
        return max((m.turn for m in self.messages), default=0)

    def character(self, character_id: str) -> Character | None:
        for c in self.characters:
            if c.id == character_id:
                return c
        return None

    def visible_messages_for(self, actor: ActorId) -> list[Message]:
        """该角色视角的消息流（R3.1——引擎 TurnContext 的原料）。"""
        joined_at = self.character_joined_at_seq.get(actor)
        return [
            m for m in self.messages
            if m.can_see(actor) and not m.control_event and not m.dependency_stale
            and (joined_at is None or m.seq >= joined_at)
        ]

    def last_turn_of(self, actor: ActorId) -> int | None:
        """该角色最后发言的回合号（导演轮盘加成用）。"""
        for m in reversed(self.messages):
            if (m.actor == actor or m.person_id == actor) and m.status == "final" and m.kind == "roleplay":
                return m.turn
        return None


class SaveFile(BaseModel):
    """存档 = 会话全量状态快照（JSON 明文，R6.5/R7.2）。"""

    schema_version: SchemaVersion = 1
    name: str = ""  # 玩家命名（2026-09-25 修复：此前未持久化，列表永远显示会话标题）
    state: SessionState
    memory_refs: list[str] = Field(default_factory=list)  # 引用的记忆记录 id
    memory_snapshot: list[MemoryRecord] = Field(default_factory=list)
    memory_snapshot_complete: bool = False
    saved_at: datetime = Field(default_factory=utcnow)


# ---------- 引擎边界 ----------


class NarrativeStyle(BaseModel):
    """会话级叙事风格；free+balanced 使用通用的自然篇幅指令。"""

    pov: Literal["free", "second", "third"] = "free"
    density: Literal["dialogue", "balanced", "atmosphere"] = "balanced"


class ReplyFrame(BaseModel):
    """Request-local description of who is speaking and what this turn can see."""

    speaker_id: str
    speaker_kind: Literal["character", "group", "other"] = "other"
    speaker_label: str
    mode: Literal["single", "serial", "parallel", "preview", "free"] = "single"
    reply_to_message_ids: list[str] = Field(default_factory=list)
    turn: int
    trigger_message_ids: list[str] = Field(default_factory=list)
    visible_prior_reply_ids: list[str] = Field(default_factory=list)
    visible_prior_speaker_labels: list[str] = Field(default_factory=list)
    actor_labels: dict[str, str] = Field(default_factory=dict)

    addressed_inputs: list[str] = Field(default_factory=list)
    other_addressed_inputs: list[str] = Field(default_factory=list)
    shared_inputs: list[str] = Field(default_factory=list)


class TurnContext(BaseModel):
    """编排核心交给引擎的一次生成请求。

    visible_messages 已过可见性裁决；injections 已按 anchor/order 排序。
    引擎（DshEngine/FakeEngine）据此组装最终 prompt（每轮重组，计划 §4）。
    """

    session_id: str
    character_id: str
    turn: int
    visible_messages: list[Message] = Field(default_factory=list)
    actor_labels: dict[str, str] = Field(default_factory=dict)
    reply_frame: ReplyFrame | None = None
    injections: list[Injection] = Field(default_factory=list)
    budget_tokens: int | None = None  # None: capacity unknown; send full visible context
    capacity_source: str = "unknown"
    capacity_fetched_at: str | None = None
    context_limit: int | None = None
    output_reserve: int | None = None
    world_core_brief: str = ""
    player_persona: str = ""
    world_runtime_policy: Literal['legacy_full', 'raw', 'compiled'] = 'legacy_full'
    # Request-only provenance, shared by direct output, DSH and inspection.
    player_identity_source: dict[str, str | None] = Field(default_factory=dict)
    reply_max_tokens: int | None = None
    style: NarrativeStyle | None = None  # R37：非默认叙事风格（None=free+balanced 零差异）
    planned_prompt: ComposedPrompt | None = None
    plan_id: str | None = None
    model_id: str = ""
    gateway: str = ""
    model_provider: str = ""
    prompt_transforms: list[dict[str, Any]] = Field(default_factory=list)
    message_id: str | None = None
    generation_id: str | None = None
    operation_id: str | None = None
    attempt_id: str | None = None
    request_ids: list[str] = Field(default_factory=list)
    provenance: GenerationProvenance | None = None


class EngineReply(BaseModel):
    """引擎返回（R2.3：整段回复 + usage）。"""

    content: str
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    # Internal engine retries can make several upstream calls. Keep the
    # individual known receipts while usage remains their aggregate.
    usage_calls: list[TokenUsage] = Field(default_factory=list)
    engine_session_id: str = ""
    raw_events: list[dict[str, Any]] = Field(default_factory=list)


class EngineHealth(BaseModel):
    """引擎健康/性能数据（M0 采集项 7；LRU 回收依据）。"""

    started_at: datetime | None = None
    last_turn_at: datetime | None = None
    turns: int = 0
    errors: int = 0
    startup_seconds: float | None = None
    rss_mb: float | None = None
