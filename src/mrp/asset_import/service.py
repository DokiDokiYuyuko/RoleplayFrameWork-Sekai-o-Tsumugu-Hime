"""Persistent review queue and validated asset commits."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mrp.engines.dsh.import_worker import IMPORT_SYSTEM_PROMPT, ImportWorker
from mrp.engines.dsh.profile import engines_root
from mrp.llm import LlmConfig, chat_text_with_usage
from mrp.settings import provider_profile_id
from mrp.shared.models import Character, CharacterCard, CharacterAuthoringSource, new_id, utcnow
from mrp.shared.model_output import json_object
from mrp.storage.atomic import read_json, write_json_atomic, write_text_atomic
from mrp.worlds.schema import ArchiveRecord, BackgroundData, BiologyData, World

TargetKind = Literal["world", "background", "biology", "character"]


class WorldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    core_brief: str = ""


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    subtype: str = ""
    aliases: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field("", max_length=5000)
    body: str = ""
    visibility: Literal["public", "private"] = "public"
    kind_data: dict[str, Any] = Field(default_factory=dict)


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", description="角色主叙述；组合整理时只有选中字段的信息迁移出去，未选字段的信息完整保留在此，不丢失自由原稿细节")
    appearance: str = Field("", description="身体与外貌：体态、发色、眼睛、衣着、辨识特征及稳定形态；保留原稿具体细节，不压缩成泛泛评价")
    traits_label: str = Field("能力与实力", description="可按题材替换的核心特质字段标题；例如能力与实力、专业技能、专长与装备、核心特质")
    traits: str = Field("", description="角色能力、实力、技能、专长或原稿明确的重要特质；保留等级、具体机制、条件、代价与限制，不要假定题材一定有超自然能力")
    personality: str = Field("", description="性格、动机与行为倾向；避免逐句重复代词")
    scenario: str = Field("", description="故事开场时的情境与人物关系")
    first_mes: str = Field("", description="仅使用原稿明确提供的角色开场台词")
    mes_example: str = Field("", description="仅使用原稿明确提供的对话示例")
    alternate_greetings: list[str] = Field(default_factory=list)
    system_prompt: str | None = None
    post_history_instructions: str | None = None
    creator_notes: str = ""
    creator: str = ""
    character_version: str = ""
    tags: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


TARGETS: dict[str, type[BaseModel]] = {
    "world": WorldInput,
    "background": ArchiveInput,
    "biology": ArchiveInput,
    "character": CharacterInput,
}
SKILL_DIR = Path(__file__).parent / "skills"


def _safe_error(exc: Exception, api_key: str) -> str:
    message = f"{type(exc).__name__}: {exc}"
    return message.replace(api_key, "[已隐藏密钥]")[:1000] if api_key else message[:1000]


def _validated(kind: str, raw: Any) -> dict[str, Any]:
    if kind not in TARGETS or not isinstance(raw, dict):
        raise ValueError("目标类型或生成数据无效")
    data = TARGETS[kind].model_validate(raw).model_dump(mode="json")
    name = data.get("name") if kind == "character" else data.get("title")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("名称不能为空")
    key = "name" if kind == "character" else "title"
    data[key] = name.strip()
    if kind in {"background", "biology"}:
        record = ArchiveRecord.model_validate({**data, "kind": kind})
        data["kind_data"] = record.kind_data
    return data


def _json_object(raw: str) -> dict[str, Any]:
    return json_object(raw)


def target_contracts() -> dict[str, Any]:
    result = {}
    for kind, model in TARGETS.items():
        schema = model.model_json_schema()
        if kind in {"background", "biology"}:
            schema["properties"]["kind_data"] = (
                BackgroundData if kind == "background" else BiologyData
            ).model_json_schema()
        digest = hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        result[kind] = {"schema": schema, "fingerprint": digest}
    return result


class ImportService:
    def __init__(self, container: Any) -> None:
        self.container = container
        self.root = container.data_root / "asset_import_jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker = ImportWorker(engines_root())
        # Direct gateway calls do not share the DSH process lock. Bound concurrent
        # imports so several drafts can progress without flooding the provider.
        self.direct_slots = asyncio.Semaphore(3)
        self.lock = asyncio.Lock()
        self.tasks: dict[str, asyncio.Task] = {}

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"import-[a-f0-9]+", job_id):
            raise KeyError(job_id)
        return self.root / f"{job_id}.json"

    def get(self, job_id: str) -> dict[str, Any]:
        job = read_json(self._path(job_id))
        if not isinstance(job, dict):
            raise KeyError(job_id)
        if job.get("status") in {"generating", "analyzing", "regenerating"} and (
            job_id not in self.tasks or self.tasks[job_id].done()
        ):
            job["interrupted_phase"] = job["status"]
            job["status"] = "interrupted"
            write_json_atomic(self._path(job_id), job)
        contracts = target_contracts()
        for draft in job.get("drafts", []):
            contract = contracts.get(draft.get("kind"))
            draft["schema_changed"] = bool(contract and draft.get("schema_fingerprint") != contract["fingerprint"])
        if isinstance(job.get("bundle"), dict):
            from .world_runtime import refresh
            job = refresh(self, job)
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("import-*.json"):
            try:
                job = self.get(path.stem)
            except KeyError:
                continue
            if isinstance(job, dict):
                rows.append({k: job.get(k) for k in (
                    "id", "title", "status", "created_at", "updated_at", "world_id", "target_kind", "intent")})
        return sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)

    def create(self, source: str, *, target_kind: str | None, world_id: str | None,
               intent: str = "organize", selected_fields: list[str] | None = None,
               instruction: str = "", target_asset_id: str | None = None,
               target_revision: int | None = None, character_seed: dict[str, Any] | None = None,
               reference_world_id: str | None = None, reference_source_ids: list[str] | None = None,
               character_aliases: list[str] | None = None,
               character_runtime: dict[str, Any] | None = None,
               source_visibility: str | None = None) -> dict[str, Any]:
        if not source.strip():
            raise ValueError("原稿不能为空")
        if source_visibility not in {None, "public", "private"}:
            raise ValueError("原稿用途无效")
        if target_kind is not None and target_kind not in TARGETS:
            raise ValueError("不支持的目标类型")
        if world_id and world_id not in self.container.worlds:
            raise ValueError("目标世界不存在")
        if selected_fields is not None:
            if target_kind != "character" or set(selected_fields) - CharacterInput.model_fields.keys():
                raise ValueError("角色整理字段无效")
            selected_fields = list(dict.fromkeys(["description", *selected_fields]))
        if target_asset_id:
            target = self.container.characters.get(target_asset_id) if target_kind == "character" else None
            if target is None:
                raise ValueError("角色整理目标不存在")
            if target.revision != target_revision:
                raise RuntimeError("目标角色已改变，请重新载入")
        reference_snapshot = None
        if reference_world_id:
            reference = self.container.worlds.get(reference_world_id)
            if reference is None:
                raise ValueError("所选参考世界不存在")
            ids = [row.id for row in reference.archive_records] if reference_source_ids is None else reference_source_ids
            if len(set(ids)) != len(ids) or set(ids) - {row.id for row in reference.archive_records}:
                raise ValueError("参考来源只能来自明确选定的世界")
            reference_snapshot = {"world_id": reference.id, "revision": reference.revision,
                "core_brief": reference.core_brief,
                "author_core_brief": reference.author_core_brief,
                "sources": [row.model_dump(mode="json") for row in reference.archive_records if row.id in ids]}
        if character_seed is not None:
            character_seed = CharacterCard.model_validate(character_seed).model_dump(mode="json")
        if character_runtime is not None:
            if set(character_runtime) - {"model", "base_url", "max_tokens"}:
                raise ValueError("角色模型配置含未知字段")
            model = str(character_runtime.get("model", "")).strip()
            base_url = str(character_runtime.get("base_url", "")).strip().rstrip("/")
            max_tokens = character_runtime.get("max_tokens", 1024)
            if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 0 <= max_tokens <= 100000:
                raise ValueError("角色最大回复长度必须是 0 到 100000 的整数；0 为自动")
            character_runtime = {"model": model, "base_url": base_url, "max_tokens": max_tokens}
        job_id = new_id("import")
        source_path = self.root / job_id / "source.txt"
        write_text_atomic(source_path, source)
        job = {
            "id": job_id, "title": source.splitlines()[0][:80],
            "status": "ready", "target_kind": target_kind,
            "world_id": world_id, "source_length": len(source),
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "drafts": [], "candidates": [], "errors": [], "created_at": utcnow().isoformat(),
            "updated_at": utcnow().isoformat(),
            "intent": intent, "selected_fields": selected_fields, "bundle_revision": 1,
            "source_visibility": source_visibility,
            "instruction": instruction,
            "target_asset_id": target_asset_id, "target_revision": target_revision,
            "character_seed": character_seed, "reference_world_id": reference_world_id,
            "reference_snapshot": reference_snapshot,
            "character_aliases": list(dict.fromkeys(character_aliases or [])),
            "character_runtime": character_runtime,
        }
        write_json_atomic(self._path(job_id), job)
        return job

    def recover_commits(self) -> list[str]:
        from mrp.lorebook_generation.commits import recover_all
        return recover_all(self.container, self.root.glob("*/bundle-*.json"))

    async def derive_world_runtime(self, job_id: str, **kwargs) -> dict[str, Any]:
        from .world_runtime import derive
        return await derive(self, job_id, **kwargs)

    async def edit_runtime_draft(self, job_id: str, **kwargs) -> dict[str, Any]:
        from .world_runtime import edit
        return await edit(self, job_id, **kwargs)

    async def commit_bundle(self, job_id: str, **kwargs) -> dict[str, Any]:
        from .world_runtime import commit
        return await commit(self, job_id, **kwargs)

    def source(self, job_id: str) -> str:
        self.get(job_id)
        return (self.root / job_id / "source.txt").read_text(encoding="utf-8")

    def _save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = utcnow().isoformat()
        write_json_atomic(self._path(job["id"]), job)

    async def start(self, job_id: str) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job.get("bundle", {}).get("status") == "committed":
                raise ValueError("此任务已通过集中审核采用，请新建修改任务")
            if job["status"] in {"generating", "analyzing", "regenerating"}:
                return job
            if job["status"] == "saved":
                raise ValueError("已保存的任务不可重新生成")
            if not job.get("candidates") and not job.get("target_kind"):
                raise ValueError("请先识别并确认候选去向")
            job["status"] = "generating"
            job.pop("interrupted_phase", None)
            job["errors"] = []
            self._save(job)
            task = asyncio.create_task(self._generate(job_id))
            self.tasks[job_id] = task
            task.add_done_callback(lambda finished: self.tasks.pop(job_id, None)
                                   if self.tasks.get(job_id) is finished else None)
            return job

    async def analyze(self, job_id: str) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job["status"] in {"analyzing", "generating", "regenerating"}:
                return job
            if job["drafts"]:
                raise ValueError("已有生成草稿，不可重新识别候选")
            job["status"] = "analyzing"
            job.pop("interrupted_phase", None)
            job["errors"] = []
            self._save(job)
            task = asyncio.create_task(self._analyze(job_id))
            self.tasks[job_id] = task
            task.add_done_callback(lambda finished: self.tasks.pop(job_id, None)
                                   if self.tasks.get(job_id) is finished else None)
            return job

    async def _analyze(self, job_id: str) -> None:
        try:
            source = self.source(job_id)
            classification = await self._call(
                (SKILL_DIR / "classify.md").read_text(encoding="utf-8") + "\n原稿：\n" + source,
                job_id, 0,
            )
            items = _json_object(classification).get("items")
            if not isinstance(items, list):
                raise ValueError("候选识别未返回 items")
            candidates = []
            for item in items[:12]:
                if not isinstance(item, dict) or item.get("kind") not in TARGETS:
                    continue
                excerpt = str(item.get("excerpt") or "").strip()
                if not excerpt or excerpt not in source:
                    excerpt = source
                candidates.append({"kind": item["kind"], "title": str(item.get("title") or "未命名")[:200],
                                   "excerpt": excerpt})
            if not candidates:
                raise ValueError("未识别到可导入的设定")
            job = self.get(job_id)
            job["candidates"] = candidates
            job["status"] = "classified"
            self._save(job)
        except Exception as exc:
            job = self.get(job_id)
            job["status"] = "failed"
            job["errors"].append("候选识别失败：" + _safe_error(exc, self.container.settings.api_key))
            self._save(job)

    async def update_candidates(self, job_id: str, candidates: list[dict[str, str]]) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job["drafts"] or job["status"] in {"analyzing", "generating", "regenerating"}:
                raise ValueError("生成开始后不能修改候选")
            if not candidates or len(candidates) > 12:
                raise ValueError("请选择 1 至 12 个候选")
            source = self.source(job_id)
            cleaned = []
            for item in candidates:
                if item.get("kind") not in TARGETS:
                    raise ValueError("候选目标类型无效")
                excerpt = str(item.get("excerpt") or "")
                if excerpt not in source or not excerpt:
                    raise ValueError("候选原文片段不属于当前原稿")
                cleaned.append({"kind": item["kind"], "title": str(item.get("title") or "未命名")[:200],
                                "excerpt": excerpt})
            job["candidates"] = cleaned
            job["status"] = "classified"
            self._save(job)
            return job

    async def _call(self, prompt: str, job_id: str, attempt: int) -> str:
        settings = self.container.settings
        if settings.engine == "openrouter":
            if not settings.api_key:
                raise ValueError("当前渠道未配置 API Key")
            config = LlmConfig(
                model=settings.auxiliary_model or settings.model,
                base_url=settings.gateway,
                api_key_env="OPENROUTER_API_KEY",
                api_key=settings.api_key,
                max_tokens=0,  # No project-imposed limit for imported source data.
                provider=(settings.auxiliary_provider
                          if provider_profile_id(settings.gateway) == "openrouter" else ""),
                provider_allow_fallbacks=settings.provider_allow_fallbacks,
            )
            async with self.direct_slots:
                answer, _usage = await asyncio.to_thread(
                    chat_text_with_usage,
                    [{"role": "system", "content": IMPORT_SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
                    config, max_tokens=0, timeout=300,
                    no_thinking=settings.thinking == "off",
                    require_complete=True,
                )
                return answer
        return await self.worker.run(
            prompt, gateway=settings.gateway, model=settings.auxiliary_model or settings.model,
            api_key=settings.api_key, session_id=f"{job_id}-{attempt}-{new_id('try')}",
        )

    async def _character_detail(self, field: str, excerpt: str, current: str,
                                job_id: str, attempt: int,
                                field_label: str = "") -> str:
        skill = (SKILL_DIR / f"character_{field}.md").read_text(encoding="utf-8")
        response = await self._call(
            f"{skill}\n\n"
            + (f"本角色的特质栏标题：{field_label.strip()}\n\n" if field == "traits" and field_label.strip() else "")
            + f"主稿当前字段（可补全，不能据此编造原稿没有的信息）：\n{current}\n\n原稿：\n{excerpt}",
            job_id, attempt,
        )
        value = _json_object(response).get("value")
        if not isinstance(value, str) or len(value) > 30000:
            raise ValueError(f"{field} 专项提取格式无效")
        return value.strip()

    async def _generate_candidate(self, job_id: str, index: int,
                                  item: dict[str, Any], source: str,
                                  world_id: str | None) -> dict[str, Any]:
        kind = item["kind"]
        excerpt = item["excerpt"]
        if kind == "background" and item.get("source_exact"):
            data = _validated("background", {
                "title": item.get("title") or "完整世界设定原稿",
                "subtype": "原稿",
                "summary": "原稿全文；可在审阅时分拆和整理，不会因模型概括而丢失。",
                "body": excerpt,
                "visibility": "private",
                "kind_data": {"section": "overview"},
            })
            evidence = excerpt[:500]
            return {
                "id": new_id("draft"), "kind": kind, "title": data["title"],
                "payload": data, "status": "needs_review", "error": "", "warnings": [],
                "evidence": evidence, "source_start": source.find(evidence),
                "source_end": source.find(evidence) + len(evidence),
                "schema_fingerprint": target_contracts()[kind]["fingerprint"],
                "world_id": world_id, "target_asset_id": None,
                "target_revision": None, "revision": 1, "saved_asset_id": None,
                "source_exact": True,
            }
        job_options = self.get(job_id)
        skill_file = "world_extend.md" if kind == "world" and job_options.get("intent") == "extend" else f"{kind}.md"
        skill = (SKILL_DIR / skill_file).read_text(encoding="utf-8")
        contract = target_contracts()[kind]
        prompt = (
            f"任务规则：\n{skill}\n\n目标 JSON Schema：\n"
            f"{json.dumps(contract['schema'], ensure_ascii=False)}\n\n"
            "返回 {\"payload\":目标对象,\"evidence\":\"原稿中的一小段原文\"}。"
            "原稿中缺少的非必填内容用默认值或空值，不能猜测。\n原稿：\n" + excerpt
        )
        if job_options.get("intent") == "extend" and kind in {"world", "background", "biology"}:
            prompt = ("本次明确要求补写新设定。可创作原稿未写出的内容；将新内容写在 payload 正文或核心中，"
                      "并在返回对象 added_facts 数组列出新增事实。新增内容只是候选，不能声称是原稿事实。\n" + prompt)
            prompt += ("\n补写要求：" + str(job_options.get("instruction") or "扩展当前设定中的有用细节")
                       + "\n对于world：额外返回 addition_text:string，只含新增正文，不复制或改写原稿。")
        if kind == "character" and job_options.get("selected_fields") is not None:
            prompt += ("\n组合整理模式：description 必选。仅将选中的字段 "
                       + json.dumps(job_options["selected_fields"], ensure_ascii=False)
                       + " 中的信息迁移到对应栏；所有未迁移的信息保留在 description，自由原稿细节不得丢失。"
                       "未选字段留空，不将作者原稿放入 extensions。")
        if kind == "character" and job_options.get("reference_snapshot"):
            prompt += ("\n用户明确选中的参考世界冻结资料，仅用于角色创作参考，"
                       "作者资料中的秘密不应自动写进共享角色设定；候选需用户核对。\n"
                       + json.dumps(job_options["reference_snapshot"], ensure_ascii=False))
        last_error = ""
        raw = ""
        data = None
        evidence = ""
        warnings: list[str] = []
        added_facts: list[str] = []
        addition_text = ""
        for attempt in range(3):
            try:
                raw = await self._call(prompt, job_id, index * 10 + attempt + 1)
                parsed = _json_object(raw)
                data = _validated(kind, parsed.get("payload"))
                if kind == "character":
                    data["extensions"] = {}
                    if job_options.get("character_seed") and job_options.get("selected_fields") is not None:
                        seed = {key: value for key, value in job_options["character_seed"].items()
                                if key in CharacterInput.model_fields}
                        fields = {"name", "description", *job_options["selected_fields"]}
                        data = _validated(kind, {**seed, **{key: value for key, value in data.items() if key in fields}})
                if job_options.get("intent") == "extend":
                    added_facts = [str(item) for item in parsed.get("added_facts", [])][:100]
                    addition_text = parsed.get("addition_text") or ""
                    if not isinstance(addition_text, str) or (kind == "world" and not addition_text.strip()):
                        raise ValueError("补写需要返回非空 addition_text；原稿不能被替换")
                else:
                    added_facts = []
                    addition_text = ""
                if kind in {"background", "biology"}:
                    # The model extracts structured fields; the original excerpt
                    # remains the authoritative full text regardless of its JSON.
                    if job_options.get("intent") != "extend":
                        data["body"] = excerpt
                evidence = str(parsed.get("evidence") or "")[:500]
                break
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                data = None
                last_error = str(exc)[:1000]
                prompt = (
                    f"上一条输出格式有误：{last_error}。按此 schema 修正 payload；"
                    "只输出一个 JSON 对象，外层必须是 {\"payload\":目标对象,\"evidence\":\"原稿原文片段\"}："
                    f"{json.dumps(contract['schema'], ensure_ascii=False)}\n"
                    f"原稿：\n{excerpt}\n上次输出：\n{raw[:12000]}"
                )
        if kind == "character" and data is not None and job_options.get("selected_fields") is None:
            detail_fields = ("appearance", "traits", "personality")
            details = await asyncio.gather(*(
                self._character_detail(
                    field, excerpt, data[field], job_id, index * 10 + 7 + detail_index,
                    str(data.get("traits_label") or "能力与实力") if field == "traits" else "",
                ) for detail_index, field in enumerate(detail_fields)
            ), return_exceptions=True)
            for field, result in zip(detail_fields, details):
                if isinstance(result, BaseException):
                    if not isinstance(result, Exception):
                        raise result
                    warnings.append(f"{field} 专项提取失败，保留主稿内容：{_safe_error(result, self.container.settings.api_key)}")
                elif result and len(result) >= len(data[field]) * 0.6:
                    data[field] = result
                elif result and data[field]:
                    warnings.append(f"{field} 专项结果过短，保留主稿内容；请核对原稿。")
            if not data["appearance"].strip() and re.search(r"外貌|体态|身高|发色|头发|眼睛|衣着|身体", excerpt):
                warnings.append("原稿疑似包含身体或外貌资料，但该字段为空，请对照原稿检查。")
            if not data["personality"].strip() and re.search(r"性格|动机|习惯|情绪|渴望|害怕|矛盾", excerpt):
                warnings.append("原稿疑似包含性格资料，但该字段为空，请对照原稿检查。")
            if not data["traits"].strip() and re.search(r"能力|实力|技能|专长|魔法|等级|特长|装备|職業|技能", excerpt, re.I):
                warnings.append("原稿疑似包含能力、实力或技能资料，但该字段为空，请对照原稿检查。")
        offset = source.find(evidence) if evidence else -1
        return {
            "id": new_id("draft"), "kind": kind,
            "title": (data or {}).get("name") or (data or {}).get("title") or item.get("title") or kind,
            "payload": data or {}, "status": "needs_review" if data else "failed",
            "error": last_error if data is None else "",
            "warnings": warnings,
            "evidence": evidence, "source_start": offset if offset >= 0 else None,
            "source_end": offset + len(evidence) if offset >= 0 else None,
            "schema_fingerprint": contract["fingerprint"],
            "world_id": world_id, "target_asset_id": job_options.get("target_asset_id"),
            "target_revision": job_options.get("target_revision"), "revision": 1, "saved_asset_id": None,
            "selected_fields": job_options.get("selected_fields"), "added_facts": added_facts,
            "intent": job_options.get("intent", "organize"),
            "addition_text": addition_text,
            "proposed_source": excerpt + ("\n\n" + addition_text if addition_text else ""),
        }

    async def _generate(self, job_id: str) -> None:
        try:
            job = self.get(job_id)
            source = self.source(job_id)
            candidates: list[dict[str, Any]] = job.get("candidates") or []
            if not candidates:
                kind = job.get("target_kind")
                if not kind:
                    raise ValueError("缺少已确认的候选去向")
                candidates = [{"kind": kind, "title": job["title"], "excerpt": source}]
                if kind == "world":
                    candidates.append({"kind": "background", "title": "完整世界设定原稿",
                                       "excerpt": source, "source_exact": True})
            job["candidates"] = candidates
            self._save(job)
            for index, item in enumerate(candidates):
                existing = job["drafts"][index] if index < len(job["drafts"]) else None
                if existing is not None and existing.get("status") != "failed":
                    continue
                draft = await self._generate_candidate(job_id, index, item, source, job.get("world_id"))
                job = self.get(job_id)
                if index < len(job["drafts"]):
                    draft["id"] = job["drafts"][index]["id"]
                    draft["revision"] = job["drafts"][index].get("revision", 1) + 1
                    job["drafts"][index] = draft
                else:
                    job["drafts"].append(draft)
                self._save(job)
            job = self.get(job_id)
            job["status"] = "needs_review"
            self._save(job)
        except Exception as exc:  # Keep already generated drafts and a resumable error.
            job = self.get(job_id)
            job["status"] = "failed"
            job["errors"].append(_safe_error(exc, self.container.settings.api_key))
            self._save(job)

    async def recover_world_source(self, job_id: str) -> dict[str, Any]:
        """For an older world-only job, create a reviewable lossless source draft."""
        async with self.lock:
            job = self.get(job_id)
            saved_world = next((draft for draft in job["drafts"]
                                if draft["kind"] == "world" and draft["status"] == "saved"), None)
            if saved_world is None or not saved_world.get("saved_asset_id"):
                raise ValueError("请先保存世界概览")
            if any(draft.get("source_exact") for draft in job["drafts"]):
                return job
            world_id = saved_world["saved_asset_id"]
            if world_id not in self.container.worlds:
                raise ValueError("已保存的世界不存在")
            source = self.source(job_id)
            candidate = {"kind": "background", "title": "完整世界设定原稿",
                         "excerpt": source, "source_exact": True}
            draft = await self._generate_candidate(job_id, len(job["drafts"]), candidate, source, world_id)
            job["candidates"].append(candidate)
            job["drafts"].append(draft)
            job["status"] = "needs_review"
            self._save(job)
            return job

    async def regenerate(self, job_id: str, draft_id: str) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job["status"] in {"analyzing", "generating", "regenerating"}:
                raise ValueError("已有生成任务正在进行")
            index = next((i for i, item in enumerate(job["drafts"]) if item["id"] == draft_id), None)
            if index is None:
                raise KeyError(draft_id)
            if job["drafts"][index]["status"] == "saved":
                raise ValueError("已保存资产不能在导入任务中重新生成")
            job["status"] = "regenerating"
            job.pop("interrupted_phase", None)
            job["regenerate_draft_id"] = draft_id
            self._save(job)
            task = asyncio.create_task(self._regenerate(job_id, index))
            self.tasks[job_id] = task
            task.add_done_callback(lambda finished: self.tasks.pop(job_id, None)
                                   if self.tasks.get(job_id) is finished else None)
            return job

    async def _regenerate(self, job_id: str, index: int) -> None:
        try:
            job = self.get(job_id)
            proposal = await self._generate_candidate(
                job_id, index + 100, job["candidates"][index],
                self.source(job_id), job["drafts"][index].get("world_id"),
            )
            job = self.get(job_id)
            job["drafts"][index]["proposal"] = {
                "payload": proposal["payload"], "error": proposal["error"],
                "evidence": proposal["evidence"],
                "warnings": proposal["warnings"],
            }
            job["status"] = "needs_review"
            job.pop("regenerate_draft_id", None)
            self._save(job)
        except Exception as exc:
            job = self.get(job_id)
            job["status"] = "needs_review"
            job.pop("regenerate_draft_id", None)
            job["errors"].append("重新生成失败：" + _safe_error(exc, self.container.settings.api_key))
            self._save(job)

    async def edit(self, job_id: str, draft_id: str, payload: dict[str, Any],
                   *, world_id: str | None, expected_revision: int,
                   target_asset_id: str | None = None,
                   target_revision: int | None = None,
                   selected_fields: list[str] | None = None) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job.get("bundle", {}).get("status") == "committed":
                raise ValueError("此任务已通过集中审核采用，请新建修改任务")
            if job_id in self.tasks and not self.tasks[job_id].done():
                raise ValueError("生成进行中，请完成后再审阅草稿")
            if job["status"] in {"generating", "analyzing", "regenerating"}:
                raise ValueError("生成进行中，请完成后再审阅草稿")
            draft = next((d for d in job["drafts"] if d["id"] == draft_id), None)
            if draft is None:
                raise KeyError(draft_id)
            if draft["status"] in {"saved", "committing"}:
                raise ValueError("已保存或正在提交的草稿不可再修改")
            if draft["revision"] != expected_revision:
                raise RuntimeError("草稿已更新，请重新载入")
            draft["payload"] = payload
            draft["title"] = str(payload.get("name") or payload.get("title") or draft["title"])
            draft["world_id"] = world_id
            draft.pop("proposal", None)
            if target_asset_id != draft.get("target_asset_id"):
                draft["saved_asset_id"] = None
            draft["target_asset_id"] = target_asset_id
            draft["target_revision"] = target_revision
            if selected_fields is not None:
                if draft["kind"] != "character" or "description" not in selected_fields:
                    raise ValueError("组合角色整理必须选中 description")
                if set(selected_fields) - CharacterInput.model_fields.keys():
                    raise ValueError("角色整理字段无效")
                draft["selected_fields"] = list(dict.fromkeys(selected_fields))
            try:
                _validated(draft["kind"], payload)
                draft["status"] = "needs_review"
                draft["error"] = ""
                draft["schema_fingerprint"] = target_contracts()[draft["kind"]]["fingerprint"]
            except (ValueError, ValidationError) as exc:
                draft["status"] = "failed"
                draft["error"] = str(exc)[:1000]
            draft["revision"] += 1
            self._save(job)
            return draft

    async def commit(self, job_id: str, draft_id: str) -> dict[str, Any]:
        async with self.lock:
            job = self.get(job_id)
            if job.get("bundle", {}).get("status") == "committed":
                raise ValueError("此任务已通过集中审核采用，不能再单独保存")
            if job_id in self.tasks and not self.tasks[job_id].done():
                raise ValueError("生成进行中，请完成后再保存草稿")
            if job["status"] in {"generating", "analyzing", "regenerating"}:
                raise ValueError("生成进行中，请完成后再保存草稿")
            draft = next((d for d in job["drafts"] if d["id"] == draft_id), None)
            if draft is None:
                raise KeyError(draft_id)
            if draft["status"] == "saved":
                return draft
            kind = draft["kind"]
            data = _validated(kind, draft["payload"])
            target_id = draft.get("target_asset_id")
            if target_id and not isinstance(draft.get("target_revision"), int):
                raise ValueError("更新已有资产必须提供当前修订号")
            if not draft.get("saved_asset_id"):
                draft["saved_asset_id"] = target_id or new_id("char" if kind == "character" else "world" if kind == "world" else "archive")
                draft["status"] = "committing"
                self._save(job)
            asset_id = draft["saved_asset_id"]
            if kind == "world":
                if target_id:
                    current = self.container.worlds.get(target_id)
                    if current is None:
                        raise ValueError("要更新的世界已不存在")
                    if current.revision == draft["target_revision"]:
                        await self.container.world_registry.revise(
                            target_id, draft["target_revision"],
                            lambda item: (setattr(item, "title", data["title"]),
                                          setattr(item, "description", data["description"]),
                                          setattr(item, "core_brief", data["core_brief"])),
                        )
                    elif current.revision != draft["target_revision"] + 1 or any(
                        getattr(current, key) != value for key, value in data.items()
                    ):
                        raise RuntimeError("目标世界已有新修改，请重新审阅差异")
                elif asset_id not in self.container.worlds:
                    await self.container.world_registry.create(World(id=asset_id, runtime_policy="raw", **data))
            elif kind == "character":
                selected_fields = draft.get("selected_fields")
                if selected_fields is not None and target_id:
                    selected_fields = list(dict.fromkeys(["description", *selected_fields]))
                    data = {key: value for key, value in data.items() if key in selected_fields or key == "name"}
                await self._commit_character(job, draft, data, asset_id, target_id)
            else:
                world_id = draft.get("world_id")
                if not world_id or world_id not in self.container.worlds:
                    raise ValueError("请先选择并保存目标世界")
                world = self.container.worlds[world_id]
                existing = next((record for record in world.archive_records if record.id == asset_id), None)
                if target_id:
                    if existing is None:
                        raise ValueError("要更新的档案已不存在")
                    if existing.kind != kind:
                        raise ValueError("档案类别不一致，不能直接改变类别")
                    if existing.revision == draft["target_revision"]:
                        def revise(item: World) -> None:
                            index = next(i for i, record in enumerate(item.archive_records) if record.id == asset_id)
                            old = item.archive_records[index]
                            item.archive_records[index] = ArchiveRecord.model_validate({
                                **old.model_dump(mode="python"), **data, "id": asset_id,
                                "kind": kind, "revision": old.revision + 1,
                                "updated_at": utcnow(),
                            })
                        await self.container.world_registry.revise(world_id, world.revision, revise)
                    elif existing.revision != draft["target_revision"] + 1 or any(
                        getattr(existing, key) != value for key, value in data.items()
                    ):
                        raise RuntimeError("目标档案已有新修改，请重新审阅差异")
                elif existing is None:
                    record = ArchiveRecord.model_validate({**data, "id": asset_id, "kind": kind})
                    await self.container.world_registry.revise(world_id, world.revision,
                                                               lambda item: item.archive_records.append(record))
            draft["status"] = "saved"
            draft["error"] = ""
            if kind == "world" and sum(item["kind"] == "world" for item in job["drafts"]) == 1:
                for item in job["drafts"]:
                    if item["kind"] in {"background", "biology"} and not item.get("world_id"):
                        item["world_id"] = asset_id
            self._save(job)
            if all(d["status"] == "saved" for d in job["drafts"]):
                job["status"] = "saved"
                self._save(job)
            return draft

    async def _commit_character(self, job: dict[str, Any], draft: dict[str, Any], data: dict[str, Any],
                                asset_id: str, target_id: str | None) -> None:
        # Share the lock with the character editor and avatar writer, then perform CAS.
        locks = getattr(self.container, "_character_media_locks", None)
        if locks is None:
            locks = self.container._character_media_locks = {}
        async with locks.setdefault(asset_id, asyncio.Lock()):
            original = self.source(job["id"])
            authoring_source = {"text": original, "captured_at": utcnow().isoformat(), "import_job_id": job["id"]}
            if target_id:
                current = self.container.characters.get(target_id)
                if current is None:
                    raise ValueError("要更新的角色已不存在")
                if current.revision == draft["target_revision"]:
                    updated = current.model_copy(deep=True)
                    updated.card = CharacterCard.model_validate({
                        **updated.card.model_dump(mode="python"), **data,
                        "extensions": {**updated.card.extensions, **data.get("extensions", {})},
                    })
                    updated.authoring_source = CharacterAuthoringSource.model_validate(authoring_source)
                    updated.source_world_id = draft.get("world_id")
                    updated.revision += 1
                    updated.updated_at = utcnow()
                    await self.container.save_character(updated)
                elif (current.revision != draft["target_revision"] + 1
                      or current.authoring_source is None
                      or current.authoring_source.import_job_id != job["id"]
                      or current.authoring_source.text != original
                      or current.source_world_id != draft.get("world_id")
                      or any(getattr(current.card, key) != value for key, value in data.items() if key != "extensions")):
                    raise RuntimeError("目标角色已有新修改，请重新审阅差异")
            elif asset_id not in self.container.characters:
                character = Character(id=asset_id, card=CharacterCard.model_validate(data),
                    authoring_source=authoring_source, source_world_id=draft.get("world_id"),
                    aliases=job.get("character_aliases", []))
                runtime = job.get("character_runtime")
                if runtime is not None:
                    character.llm.model = runtime["model"]
                    character.llm.inherit_model = not character.llm.model
                    character.llm.base_url = runtime["base_url"]
                    character.llm.inherit_base_url = not character.llm.base_url
                    character.llm.sampling = {**character.llm.sampling, "max_tokens": runtime["max_tokens"]}
                await self.container.save_character(character)

    async def fail_commit(self, job_id: str, draft_id: str, cause: Exception) -> None:
        async with self.lock:
            try:
                job = self.get(job_id)
            except KeyError:
                return
            draft = next((item for item in job["drafts"] if item["id"] == draft_id), None)
            if draft is not None and draft["status"] == "committing":
                draft["status"] = "needs_review"
                draft["error"] = _safe_error(cause, self.container.settings.api_key)
                self._save(job)

    async def delete(self, job_id: str) -> None:
        path = self._path(job_id)
        if not path.exists():
            raise KeyError(job_id)
        current = self.tasks.get(job_id)
        running = [current] if current is not None and not current.done() else []
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        async with self.lock:
            source_dir = (self.root / job_id).resolve()
            if not source_dir.is_relative_to(self.root.resolve()):
                raise ValueError("非法导入任务路径")
            from mrp.lorebook_generation.commits import ensure_no_pending
            ensure_no_pending(source_dir.glob("bundle-*.json"))
            if source_dir.exists():
                await asyncio.to_thread(shutil.rmtree, source_dir)
            path.unlink(missing_ok=True)

    async def close(self) -> None:
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.worker.close()
