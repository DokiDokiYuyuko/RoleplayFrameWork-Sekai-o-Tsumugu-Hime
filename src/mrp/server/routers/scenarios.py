"""场景预设 API：独立于角色/世界书迁移包的可复用剧情配置。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from mrp.scenarios.codec import decode_package, encode_package
from mrp.scenarios.schema import ScenarioCreateRequest, ScenarioPackage, ScenarioPatchRequest
from mrp.scenarios.service import package_from_library
from mrp.server.container import AppContainer
from mrp.server.deps import get_container, state_dict
from mrp.shared.models import Lorebook, LorebookEntry, new_id, utcnow
from mrp.storage.naming import content_disposition
from mrp.worlds.schema import World

router = APIRouter()


async def _get(container: AppContainer, scenario_id: str) -> ScenarioPackage:
    try:
        package = await container.scenarios.get(scenario_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if package is None:
        raise HTTPException(404, "场景预设不存在")
    return package


@router.get("/api/v1/scenarios")
async def list_scenarios(container: AppContainer = Depends(get_container)):
    return [row.model_dump(mode="json") for row in await container.scenarios.list()]


@router.post("/api/v1/scenarios")
async def create_scenario(
    request: ScenarioCreateRequest, container: AppContainer = Depends(get_container)
):
    try:
        package = package_from_library(request, container)
        await container.scenarios.save(package)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return package.model_dump(mode="json")


@router.get("/api/v1/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, container: AppContainer = Depends(get_container)):
    return (await _get(container, scenario_id)).model_dump(mode="json")


@router.post("/api/v1/scenarios/{scenario_id}/save-world")
async def save_scenario_world(scenario_id: str, container: AppContainer = Depends(get_container)):
    package = await _get(container, scenario_id)
    component = package.components.get("mrp.world_archive")
    if component is None or component.version != 1:
        raise HTTPException(400, "这份预设没有可另存的世界档案")
    try:
        world = World.model_validate(component.data.get("world"))
    except ValueError as exc:
        raise HTTPException(400, f"世界档案格式无效：{exc}") from exc
    old_world_id = world.id
    source_world_id = world.source_asset_id or world.id
    source_world_revision = world.source_asset_revision or world.revision
    bindings = component.data.get("book_bindings", {})
    if not isinstance(bindings, dict):
        raise HTTPException(400, "世界书映射格式无效")
    portable_books = {item.key: item for item in package.lorebooks}
    fresh_books: list[Lorebook] = []
    fresh_ids: list[str] = []
    world.id = new_id("world")
    world.source_asset_id = source_world_id
    world.source_asset_revision = source_world_revision
    world.revision = 1
    world.created_at = utcnow()
    world.updated_at = world.created_at
    for old_book_id in world.lorebook_ids:
        key = bindings.get(old_book_id)
        source = portable_books.get(key)
        if source is None:
            continue  # 预设只保留了实际打包的书；不回连原素材库。
        book = Lorebook(
            source_asset_id=source.source_asset_id,
            source_asset_revision=source.source_asset_revision,
            name=source.name, description=source.description,
            entries=[LorebookEntry.model_validate(item.model_dump(mode="python")) for item in source.entries],
            scan_depth=source.scan_depth, token_budget=source.token_budget,
            recursive_scanning=source.recursive_scanning, source_format=source.source_format,
        )
        for entry in book.entries:
            source_ref = entry.extensions.get("mrp.archive_source")
            if isinstance(source_ref, dict) and source_ref.get("world_id") == old_world_id:
                source_ref["world_id"] = world.id
        fresh_books.append(book)
        fresh_ids.append(book.id)
    world.lorebook_ids = fresh_ids
    saved_books: list[Lorebook] = []
    try:
        for book in fresh_books:
            await container.save_lorebook(book)
            saved_books.append(book)
        await container.world_registry.create(world)
    except Exception:
        for book in saved_books:
            await container.lorebook_registry.delete(book.id)
        raise
    return world.model_dump(mode="json")


@router.patch("/api/v1/scenarios/{scenario_id}")
async def patch_scenario(
    scenario_id: str,
    request: ScenarioPatchRequest,
    container: AppContainer = Depends(get_container),
):
    package = await _get(container, scenario_id)
    if request.expected_revision is not None and request.expected_revision != package.revision:
        raise HTTPException(409, "预设已在另一处更新，请重新载入后重试")
    patch = {
        key: value
        for key, value in request.model_dump(exclude_unset=True).items()
        if key != "expected_revision" and value is not None
    }
    package = package.model_copy(update=patch)
    package.revision += 1
    package.updated_at = utcnow()
    await container.scenarios.save(package)
    return package.model_dump(mode="json")


@router.delete("/api/v1/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str, container: AppContainer = Depends(get_container)):
    try:
        deleted = await container.scenarios.delete(scenario_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "场景预设不存在")
    return {"ok": True}


@router.get("/api/v1/scenarios/{scenario_id}/export")
async def export_scenario(scenario_id: str, container: AppContainer = Depends(get_container)):
    package = await _get(container, scenario_id)
    data = encode_package(package)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(package.title, ".mrpscenario", fallback="scenario")},
    )


@router.post("/api/v1/scenarios/import")
async def import_scenario(file: UploadFile, container: AppContainer = Depends(get_container)):
    raw = await file.read()
    try:
        package = decode_package(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 导入永远生成新条目，防止同 ID 文件覆盖本地预设。
    package.id = new_id("scenario")
    package.imported_at = datetime.now(timezone.utc)
    package.updated_at = package.imported_at
    await container.scenarios.save(package)
    return package.model_dump(mode="json")


class StartScenarioReq(BaseModel):
    opening_scene: str = ""


@router.post("/api/v1/scenarios/{scenario_id}/start")
async def start_scenario(scenario_id: str, req: StartScenarioReq | None = None,
                         container: AppContainer = Depends(get_container),
                         operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    from mrp.server.creation_commands import execute_creation
    from mrp.server.command_ids import command_id, conflict_detail
    opening_scene = req.opening_scene if req else ""
    made = []
    async def create():
        package = await _get(container, scenario_id)
        runner = await container.create_scenario_session(package, opening_scene=opening_scene, register=False)
        made.append(runner)
        await container.sessions.claim_creation_entity('branch', runner.state.meta.id)
        if not opening_scene.strip():
            await runner.open_round()
        await container.persist_session(runner)
        return state_dict(runner)
    try:
        result = await execute_creation(container, 'story.create-scenario',
            {'scenario_id':scenario_id, 'opening_scene':opening_scene}, command_id(operation_header), create, response=response)
        if made:
            await container._register_runner(made[0])
        return result
    except ValueError as exc:
        raise HTTPException(409 if getattr(exc, 'command_conflict', False) else 400, conflict_detail(exc)) from exc
    finally:
        if made and container.runners.get(made[0].state.meta.id) is not made[0]:
            await made[0].aclose()
