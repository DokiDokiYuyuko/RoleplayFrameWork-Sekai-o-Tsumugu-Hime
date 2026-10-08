"""Bounded scene conversations; the HTTP connection does not own execution."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from mrp.server.container import AppContainer
from mrp.server.deps import get_container, runner_or_404

router = APIRouter()


class StartConversationReq(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    participant_ids: list[str] = Field(min_length=1, max_length=40)
    max_replies: int = Field(default=6, ge=1, le=30)
    directive: str = Field(default="", max_length=4000)
    expected_branch_revision: int = Field(ge=0)
    expected_player_identity_id: str | None


class ResumeConversationReq(BaseModel):
    expected_branch_revision: int = Field(ge=0)
    expected_player_identity_id: str | None
    additional_replies: int | None = Field(default=None, ge=1, le=30)


@router.post("/api/v1/sessions/{session_id}/conversation-runs")
async def start(session_id: str, req: StartConversationReq, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return (await container.conversation_runs.start(r, **req.model_dump())).model_dump(mode="json")


@router.get("/api/v1/sessions/{session_id}/conversation-runs")
async def list_runs(session_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return {"runs": [x.model_dump(mode="json") for x in r.state.conversation_runs if x.session_id == session_id]}


@router.get("/api/v1/sessions/{session_id}/conversation-runs/{run_id}")
async def get_run(session_id: str, run_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return (await container.conversation_runs.get(r, run_id)).model_dump(mode="json")


@router.post("/api/v1/sessions/{session_id}/conversation-runs/{run_id}/pause")
async def pause(session_id: str, run_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return (await container.conversation_runs.pause(r, run_id)).model_dump(mode="json")


@router.post("/api/v1/sessions/{session_id}/conversation-runs/{run_id}/stop")
async def stop(session_id: str, run_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return (await container.conversation_runs.stop(r, run_id)).model_dump(mode="json")


@router.post("/api/v1/sessions/{session_id}/conversation-runs/{run_id}/resume")
async def resume(session_id: str, run_id: str, req: ResumeConversationReq, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return (await container.conversation_runs.resume(r, run_id, **req.model_dump())).model_dump(mode="json")
