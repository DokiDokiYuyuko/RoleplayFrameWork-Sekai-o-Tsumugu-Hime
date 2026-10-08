"""World-scoped organization, editing and explicit draft adoption."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.world_organize.schemas import BatchCommitInput, CreateJobInput, DraftPatchInput

router = APIRouter(prefix="/api/v1/world-organize-jobs")


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "整理任务或草稿不存在")
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


@router.get("")
async def list_jobs(world_id: str | None = None, container: AppContainer = Depends(get_container)):
    return container.world_organize.list_jobs(world_id)


@router.post("")
async def create_job(req: CreateJobInput, container: AppContainer = Depends(get_container)):
    try:
        return container.world_organize.create(req)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/{job_id}")
async def get_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.world_organize.get(job_id)
    except KeyError as exc:
        raise _error(exc) from exc


@router.get("/{job_id}/source")
async def get_source(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.world_organize.source(job_id)
    except KeyError as exc:
        raise _error(exc) from exc


@router.patch("/{job_id}/drafts/{draft_id}")
async def edit_draft(job_id: str, draft_id: str, req: DraftPatchInput,
                     container: AppContainer = Depends(get_container)):
    try:
        return await container.world_organize.edit_draft(job_id, draft_id, **req.model_dump())
    except (KeyError, ValueError, RuntimeError, ValidationError) as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return await container.world_organize.cancel(job_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return await container.world_organize.resume(job_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/commit-batch")
async def commit_batch(job_id: str, req: BatchCommitInput, container: AppContainer = Depends(get_container)):
    try:
        return await container.world_organize.commit_batch(job_id, **req.model_dump())
    except (KeyError, ValueError, RuntimeError, ValidationError) as exc:
        raise _error(exc) from exc
