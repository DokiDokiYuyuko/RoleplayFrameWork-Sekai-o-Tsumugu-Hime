"""Compatibility bridge for optional HTTP command identities."""
import re

from fastapi import HTTPException


def command_id(header, body=None):
    header = header if isinstance(header, str) else None
    if header and body and header != body:
        raise HTTPException(400, "请求头与正文操作 ID 不一致")
    value = header or body
    if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise HTTPException(400, "无效的操作 ID")
    return value


def command_payload(request):
    return request.model_dump(mode="json", exclude_unset=True, exclude={"operation_id"})


def conflict_detail(error):
    return {"code": getattr(error, "code", "message_conflict"), "message": str(error)}


def command_headers(response, metadata):
    if response is not None and metadata:
        response.headers["X-Command-ID"] = metadata["operation_id"]
        response.headers["X-Branch-Revision"] = str(metadata["branch_revision"])


async def execute_command(container, runner, action, payload, mutate, *, response=None, **kwargs):
    from mrp.application.branch_commit import BranchCommitConflict
    from mrp.server.story_transport import command_projection
    async def wire_result():
        return command_projection(await mutate())
    metadata = {}
    try:
        result = await container.branch_commit.execute(runner, action, payload, wire_result,
            response_meta=metadata, **kwargs)
    except BranchCommitConflict as exc:
        raise HTTPException(exc.status_code, conflict_detail(exc)) from exc
    command_headers(response, metadata)
    return result
