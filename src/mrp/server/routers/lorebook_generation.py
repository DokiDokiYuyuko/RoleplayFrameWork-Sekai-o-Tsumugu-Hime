"""Reviewable DSH Agent jobs for turning world archive material into lorebooks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from mrp.lorebook_generation.schemas import BatchCommitInput, CommitInput, CreateJobInput, DraftPatchInput
from mrp.server.container import AppContainer
from mrp.server.deps import get_container

router = APIRouter(prefix="/api/v1/lorebook-agent-jobs")


@router.get("")
async def list_jobs(container: AppContainer = Depends(get_container)):
    return container.lorebook_generation.repo.list()


@router.post("")
async def create_job(req: CreateJobInput, container: AppContainer = Depends(get_container)):
    try:
        return container.lorebook_generation.create(req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/commit-batch")
async def commit_batch(job_id: str, req: BatchCommitInput, container: AppContainer = Depends(get_container)):
    try:
        return await container.lorebook_generation.commit_batch(job_id, **req.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "世界书任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{job_id}")
async def get_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.lorebook_generation.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 任务不存在") from exc


@router.get("/{job_id}/sources")
async def get_sources(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.lorebook_generation.sources(job_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 任务不存在") from exc


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        await container.lorebook_generation.cancel(job_id)
        return container.lorebook_generation.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 任务不存在") from exc


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.lorebook_generation.resume(job_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{job_id}")
async def delete_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        await container.lorebook_generation.delete(job_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{job_id}/drafts/{draft_id}")
async def edit_draft(job_id: str, draft_id: str, req: DraftPatchInput,
                     container: AppContainer = Depends(get_container)):
    try:
        async with container.lorebook_generation._lock:
            return container.lorebook_generation.edit_draft(
                job_id, draft_id, req.payload,
                expected_revision=req.expected_revision,
                patch=req.model_dump(exclude={"expected_revision", "payload"}),
            )
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 草稿不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/simulate")
async def simulate_draft(job_id: str, draft_id: str,
                         container: AppContainer = Depends(get_container)):
    try:
        async with container.lorebook_generation._lock:
            return container.lorebook_generation.simulate_draft(job_id, draft_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 草稿不存在") from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/regenerate")
async def regenerate_draft(job_id: str, draft_id: str,
                           container: AppContainer = Depends(get_container)):
    try:
        async with container.lorebook_generation._lock:
            return await container.lorebook_generation.regenerate_draft(job_id, draft_id)
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 草稿不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/commit")
async def commit_draft(job_id: str, draft_id: str, req: CommitInput,
                       container: AppContainer = Depends(get_container)):
    try:
        return await container.lorebook_generation.commit_draft(
            job_id, draft_id, expected_revision=req.expected_revision,
            accept_source_changes=req.accept_source_changes,
        )
    except KeyError as exc:
        raise HTTPException(404, "世界书 Agent 草稿不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc

