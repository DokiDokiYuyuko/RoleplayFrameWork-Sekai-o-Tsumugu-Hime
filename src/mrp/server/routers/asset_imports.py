"""AI asset import jobs. Generating a draft never changes the asset library."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from mrp.asset_import.service import target_contracts
from mrp.server.container import AppContainer
from mrp.server.deps import get_container

router = APIRouter(prefix="/api/v1/asset-import-jobs")


class CreateReq(BaseModel):
    source: str = Field(min_length=1)
    target_kind: str | None = None
    world_id: str | None = None
    intent: Literal["organize", "extend"] = "organize"
    source_visibility: Literal["public", "private"] | None = None
    selected_fields: list[str] | None = None
    instruction: str = Field("", max_length=8000)
    target_asset_id: str | None = None
    target_revision: int | None = Field(None, ge=1)
    character_seed: dict[str, Any] | None = None
    reference_world_id: str | None = None
    reference_source_ids: list[str] | None = None
    character_aliases: list[str] = Field(default_factory=list, max_length=30)
    character_runtime: dict[str, Any] | None = None


class EditReq(BaseModel):
    payload: dict[str, Any]
    world_id: str | None = None
    expected_revision: int = Field(ge=1)
    target_asset_id: str | None = None
    target_revision: int | None = Field(default=None, ge=1)
    selected_fields: list[str] | None = None


class DeriveRuntimeReq(BaseModel):
    expected_bundle_revision: int = Field(1, ge=1)
    source_ids: list[str] | None = None
    target_lorebook_id: str | None = None
    new_lorebook_name: str = ""


class RuntimeDraftReq(BaseModel):
    expected_bundle_revision: int = Field(ge=1)
    core_content: str | None = None
    manuscript_body: str | None = None
    proposal_id: str | None = None
    payload: dict[str, Any] | None = None
    action: Literal["add", "replace", "disable", "keep"] | None = None
    resolve_conflict: bool = False
    target_uid: int | None = None
    positive_examples: list[str] | None = Field(None, max_length=20)
    negative_examples: list[str] | None = Field(None, max_length=20)


class BundleCommitReq(BaseModel):
    operation_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_bundle_revision: int = Field(ge=1)
    review_digest: str
    expected_world_revision: int | None = Field(None, ge=1)
    expected_lorebook_revision: int | None = Field(None, ge=1)
    accepted_proposal_ids: list[str] = Field(default_factory=list)
    shared_proposal_ids: list[str] = Field(default_factory=list)
    runtime_mode: Literal["raw", "compiled"] = "compiled"


class CandidatesReq(BaseModel):
    candidates: list[dict[str, str]] = Field(min_length=1, max_length=12)


@router.get("/targets")
async def contracts():
    return target_contracts()


@router.get("")
async def list_jobs(container: AppContainer = Depends(get_container)):
    return container.asset_imports.list_jobs()


@router.post("")
async def create_job(req: CreateReq, container: AppContainer = Depends(get_container)):
    try:
        return container.asset_imports.create(req.source, target_kind=req.target_kind, world_id=req.world_id,
                                             intent=req.intent, selected_fields=req.selected_fields,
                                             source_visibility=req.source_visibility,
                                             instruction=req.instruction, target_asset_id=req.target_asset_id,
                                             target_revision=req.target_revision, character_seed=req.character_seed,
                                             reference_world_id=req.reference_world_id,
                                             reference_source_ids=req.reference_source_ids,
                                             character_aliases=req.character_aliases,
                                             character_runtime=req.character_runtime)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{job_id}")
async def get_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.asset_imports.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc


@router.delete("/{job_id}")
async def delete_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        await container.asset_imports.delete(job_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{job_id}/source")
async def get_source(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return {"source": container.asset_imports.source(job_id)}
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc


@router.post("/{job_id}/generate")
async def generate(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.start(job_id)
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/recover-world-source")
async def recover_world_source(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.recover_world_source(job_id)
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/analyze")
async def analyze(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.analyze(job_id)
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{job_id}/candidates")
async def update_candidates(job_id: str, req: CandidatesReq,
                            container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.update_candidates(job_id, req.candidates)
    except KeyError as exc:
        raise HTTPException(404, "导入任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{job_id}/drafts/{draft_id}")
async def edit_draft(job_id: str, draft_id: str, req: EditReq,
                     container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.edit(
            job_id, draft_id, req.payload, world_id=req.world_id,
            expected_revision=req.expected_revision,
            target_asset_id=req.target_asset_id, target_revision=req.target_revision,
            selected_fields=req.selected_fields,
        )
    except KeyError as exc:
        raise HTTPException(404, "草稿不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/regenerate")
async def regenerate_draft(job_id: str, draft_id: str,
                           container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.regenerate(job_id, draft_id)
    except KeyError as exc:
        raise HTTPException(404, "草稿不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/commit")
async def commit_draft(job_id: str, draft_id: str,
                       container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.commit(job_id, draft_id)
    except KeyError as exc:
        raise HTTPException(404, "草稿不存在") from exc
    except RuntimeError as exc:
        await container.asset_imports.fail_commit(job_id, draft_id, exc)
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        await container.asset_imports.fail_commit(job_id, draft_id, exc)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        await container.asset_imports.fail_commit(job_id, draft_id, exc)
        raise HTTPException(502, "资产保存失败，请重试；草稿已保留") from exc


@router.post("/{job_id}/derive-world-runtime")
async def derive_runtime(job_id: str, req: DeriveRuntimeReq, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.derive_world_runtime(job_id, **req.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "任务或目标不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{job_id}/runtime-draft")
async def edit_runtime(job_id: str, req: RuntimeDraftReq, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.edit_runtime_draft(job_id, **req.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "任务或候选不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/commit-bundle")
async def commit_runtime(job_id: str, req: BundleCommitReq, container: AppContainer = Depends(get_container)):
    try:
        return await container.asset_imports.commit_bundle(job_id, **req.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "任务或目标不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/resume-world-runtime")
async def resume_runtime(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        job = container.asset_imports.get(job_id)
        bundle = job.get("bundle") or {}
        if not bundle.get("child_job_id") or bundle.get("source_edited"):
            raise ValueError("没有可恢复的提炼任务；原稿改变后请重新提炼")
        container.lorebook_generation.resume(bundle["child_job_id"])
        return container.asset_imports.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
