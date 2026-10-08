"""Explicit adapters between library resources, portable scenarios, and session snapshots."""
from __future__ import annotations

from mrp.shared.models import Character, CharacterCard, Lorebook, LorebookEntry, Message, ModelConfig, Scene, SessionMeta, SessionState, new_id

from .schema import (
    ScenarioCharacter,
    ScenarioCharacterCard,
    ScenarioCreateRequest,
    ScenarioLorebook,
    ScenarioLorebookEntry,
    ScenarioOpening,
    ScenarioPackage,
    ScenarioComponent,
)
from mrp.worlds.schema import World


def package_from_library(request: ScenarioCreateRequest, container) -> ScenarioPackage:
    world = container.worlds.get(request.world_id) if request.world_id else None
    if request.world_id and world is None:
        raise ValueError("所选世界不存在")
    if world is not None and world.archived:
        raise ValueError("归档世界不能用于新预设")
    if len(request.character_ids) != len(set(request.character_ids)):
        raise ValueError("同一个角色不能重复选择")
    if len(request.lorebook_ids) != len(set(request.lorebook_ids)):
        raise ValueError("同一本世界书不能重复选择")
    missing_characters = [cid for cid in request.character_ids if cid not in container.characters]
    requested_books = list(request.lorebook_ids)
    for character_id in request.character_ids:
        character = container.characters.get(character_id)
        if character is None:
            continue
        for book_id in character.bound_lorebook_ids:
            if book_id in container.lorebooks and book_id not in requested_books:
                requested_books.append(book_id)
    missing_books = [bid for bid in request.lorebook_ids if bid not in container.lorebooks]
    if missing_characters:
        raise ValueError("有角色已不存在，请刷新列表后重试")
    if missing_books:
        raise ValueError("有世界书已不存在，请刷新列表后重试")

    # Portable references use stable source IDs, never labels or list positions.
    book_key_by_id = {book_id: f"book-{book_id}" for book_id in requested_books}
    books: list[ScenarioLorebook] = []
    for book_id in requested_books:
        book = container.lorebooks[book_id]
        books.append(
            ScenarioLorebook(
                key=book_key_by_id[book_id],
                source_asset_id=book.id,
                source_asset_revision=book.revision,
                name=book.name,
                description=book.description,
                entries=[ScenarioLorebookEntry.model_validate(entry.model_dump(mode="python")) for entry in book.entries],
                scan_depth=book.scan_depth,
                token_budget=book.token_budget,
                recursive_scanning=book.recursive_scanning,
                source_format=book.source_format,
                **(book.model_extra or {}),
            )
        )

    cast: list[ScenarioCharacter] = []
    actor_keys: list[str] = []
    for character_id in request.character_ids:
        character = container.characters[character_id]
        key = f"character-{character.id}"
        actor_keys.append(key)
        card = ScenarioCharacterCard.model_validate(
            character.card.model_dump(mode="python", exclude={"avatar_path"})
        )
        cast.append(
            ScenarioCharacter(
                key=key,
                card=card,
                source_asset_id=character.id,
                source_asset_revision=character.revision,
                aliases=list(character.aliases),
                talkativeness=character.talkativeness,
                interject_enabled=character.interject_enabled,
                followup_enabled=character.followup_enabled,
                **(character.model_extra or {}),
                lorebook_keys=[
                    book_key_by_id[bid]
                    for bid in character.bound_lorebook_ids
                    if bid in book_key_by_id
                ],
            )
        )

    opening = request.opening.model_copy(deep=True)
    if not opening.member_keys:
        opening.member_keys = actor_keys.copy()
    unknown_members = set(opening.member_keys) - set(actor_keys)
    if unknown_members:
        raise ValueError("开场场景引用了未选择的角色")
    return ScenarioPackage(
        title=request.title,
        description=request.description,
        author=request.author,
        license=request.license,
        source_url=request.source_url,
        tags=request.tags,
        player_persona=request.player_persona,
        instructions=request.instructions,
        cast=cast,
        lorebooks=books,
        opening=opening,
        play=request.play,
        components={"mrp.world_archive": ScenarioComponent(version=1, data={
            "world": world.model_dump(mode="json"),
            "book_bindings": {book_id: book_key_by_id[book_id] for book_id in world.lorebook_ids if book_id in book_key_by_id},
        })} if world else {},
    )


def instantiate(package: ScenarioPackage) -> SessionState:
    """Create a detached, session-owned snapshot; package-local keys become fresh runtime IDs."""
    unsupported = [name for name, component in package.components.items() if component.required and name != "mrp.world_archive"]
    if unsupported:
        raise ValueError("当前版本缺少场景包要求的功能模块：" + "、".join(sorted(unsupported)))
    actor_ids = {member.key: new_id("char") for member in package.cast}
    book_ids = {book.key: new_id("book") for book in package.lorebooks}
    known_actor_keys = set(actor_ids)
    known_book_keys = set(book_ids)
    if set(package.opening.member_keys) - known_actor_keys:
        raise ValueError("开场场景引用了不存在的角色")
    if any(set(member.lorebook_keys) - known_book_keys for member in package.cast):
        raise ValueError("有角色绑定了不存在的世界书")

    books = [
        Lorebook(
            id=book_ids[item.key],
            source_asset_id=item.source_asset_id,
            source_asset_revision=item.source_asset_revision,
            name=item.name,
            description=item.description,
        entries=[LorebookEntry.model_validate(entry.model_dump(mode="python")) for entry in item.entries],
            scan_depth=item.scan_depth,
            token_budget=item.token_budget,
            recursive_scanning=item.recursive_scanning,
            source_format=item.source_format,
            **(item.model_extra or {}),
        )
        for item in package.lorebooks
    ]
    members: list[Character] = []
    present_keys = set(package.opening.member_keys)
    for item in package.cast:
        card = CharacterCard.model_validate(item.card.model_dump(mode="python"))
        members.append(
            Character(
                id=actor_ids[item.key],
                source_asset_id=item.source_asset_id,
                source_asset_revision=item.source_asset_revision,
                card=card,
                aliases=list(item.aliases),
                bound_lorebook_ids=[book_ids[key] for key in item.lorebook_keys],
                # 连接模型始终来自本机全局设置，不从场景包读取。
                llm=ModelConfig(),
                talkativeness=item.talkativeness,
                muted=False,
                present=item.key in present_keys,
                interject_enabled=item.interject_enabled,
                followup_enabled=item.followup_enabled,
                **(item.model_extra or {}),
            )
        )

    scene = Scene(
        title=package.opening.location,
        description=package.opening.description,
        member_ids=[actor_ids[key] for key in package.opening.member_keys],
    )
    play = package.play
    meta = SessionMeta(
        title=package.title,
        player_persona=package.player_persona,
        character_ids=[member.id for member in members],
        lorebook_ids=[book.id for book in books],
        scenario_instructions=package.instructions,
        source_scenario_id=package.id,
        source_package_id=package.package_id,
        source_package_revision=package.revision,
    )
    world_component = package.components.get("mrp.world_archive")
    if world_component is not None:
        if world_component.version != 1:
            raise ValueError("场景包中的世界档案版本尚不支持")
        world = World.model_validate(world_component.data.get("world"))
        meta.source_world_id = world.id
        meta.source_world_revision = world.revision
        meta.world_core_brief = world.core_brief
        meta.world_runtime_policy = world.runtime_policy
        meta.world_archive_records = [
            record.model_dump(mode="json") for record in world.archive_records
        ]
    for field in (
        "narrative_pov",
        "narrative_density",
        "director_mode",
        "options_enabled",
        "options_style",
        "proactive_turn_limit",
    ):
        value = getattr(play, field)
        if value is not None:
            setattr(meta, field, value)
    messages = []
    if package.opening.narration.strip():
        messages.append(
            Message(
                session_id=meta.id,
                seq=0,
                turn=0,
                actor="director",
                content=package.opening.narration.strip(),
                kind="scene",
                visible_to="all",
                scene_id=scene.id,
            )
        )
    return SessionState(
        schema_version=3,
        meta=meta,
        messages=messages,
        characters=members,
        lorebooks=books,
        scenes=[scene],
        active_scene_id=scene.id,
    )
