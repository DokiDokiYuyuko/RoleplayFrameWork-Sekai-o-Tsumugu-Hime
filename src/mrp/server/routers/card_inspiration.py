from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from mrp.card_inspiration.schemas import (
    AgentTurnInput, BriefPatchInput, CommitInput, CreateJobInput, DraftPatchInput, GenerateInput,
)
from mrp.card_inspiration.service import CardInspirationService
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.card_discovery.transport import SourceRequestError

router = APIRouter(prefix="/api/v1/card-inspiration-jobs")


@router.get("")
async def list_jobs(container: AppContainer = Depends(get_container)):
    return container.card_inspiration.repo.list()


@router.post("")
async def create_job(req: CreateJobInput, container: AppContainer = Depends(get_container)):
    try:
        return await container.card_inspiration.create(req)
    except (ValueError, SourceRequestError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{job_id}")
async def get_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        return container.card_inspiration.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "角色卡灵感任务不存在") from exc


@router.post("/{job_id}/generate")
async def generate_drafts(job_id: str, req: GenerateInput, container: AppContainer = Depends(get_container)):
    try:
        return container.card_inspiration.start_generation(job_id, req.count)
    except KeyError as exc:
        raise HTTPException(404, "角色卡灵感任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{job_id}/brief")
async def update_brief(job_id: str, req: BriefPatchInput, container: AppContainer = Depends(get_container)):
    try:
        return container.card_inspiration.update_brief(job_id, req)
    except KeyError as exc:
        raise HTTPException(404, "角色卡灵感任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/agent/turn")
async def agent_turn(job_id: str, req: AgentTurnInput, container: AppContainer = Depends(get_container)):
    try:
        return container.card_inspiration.start_agent_turn(job_id, req.message)
    except KeyError as exc:
        raise HTTPException(404, "角色卡灵感任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{job_id}/drafts/{draft_id}")
async def edit_draft(job_id: str, draft_id: str, req: DraftPatchInput, container: AppContainer = Depends(get_container)):
    try:
        return container.card_inspiration.edit_draft(
            job_id, draft_id, req.payload, req.aliases, req.expected_revision, req.source,
        )
    except KeyError as exc:
        raise HTTPException(404, "角色草稿不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{job_id}/drafts/{draft_id}/commit")
async def commit_draft(job_id: str, draft_id: str, req: CommitInput, container: AppContainer = Depends(get_container)):
    try:
        return await container.card_inspiration.commit_draft(
            job_id, draft_id, req.payload, req.aliases, req.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(404, "角色草稿不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{job_id}")
async def delete_job(job_id: str, container: AppContainer = Depends(get_container)):
    try:
        await container.card_inspiration.delete(job_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, "角色卡灵感任务不存在") from exc
