"""Copy biology archive records from one world into another.

The source world is never written. A later import recognizes a copy only by
provenance (source world id + source archive id). A matching title is a
different record and is never overwritten silently.
"""

from __future__ import annotations

from mrp.shared.models import utcnow
from mrp.worlds.schema import ArchiveRecord, World

_MODES = {"copy", "overwrite"}


def apply_biology_import(destination: World, source: World, items: list[tuple[str, str]]) -> None:
    """Mutate ``destination`` only after every item has been checked.

    ``items`` is ``(source_archive_id, mode)`` with mode ``copy`` or ``overwrite``.
    One call corresponds to one destination revision bump, applied by the caller.
    """
    if destination.archived:
        raise ValueError("归档世界不能修改")
    if not source.id or source.id == destination.id:
        raise ValueError("不能从世界自身导入")
    if not items:
        raise ValueError("请选择要导入的生物设定")
    if len(items) > 200:
        raise ValueError("一次最多导入 200 条生物设定")
    for _item_id, mode in items:
        if mode not in _MODES:
            raise ValueError("导入方式无效")
    item_ids = [item_id for item_id, _mode in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("要导入的生物设定重复了")

    source_by_id = {record.id: record for record in source.archive_records}
    for item_id in item_ids:
        record = source_by_id.get(item_id)
        if record is None or record.kind != "biology":
            raise ValueError("要导入的生物设定不存在或已删除")

    provenance: dict[tuple[str, str], list[ArchiveRecord]] = {}
    for record in destination.archive_records:
        if record.copied_from_world_id and record.copied_from_archive_id:
            key = (record.copied_from_world_id, record.copied_from_archive_id)
            provenance.setdefault(key, []).append(record)
    if any(len(rows) > 1 for rows in provenance.values()):
        raise ValueError("目标世界里同一来源已有多份副本")

    copies: list[ArchiveRecord] = []
    overwrites: list[tuple[ArchiveRecord, ArchiveRecord]] = []
    for item_id, mode in items:
        src = source_by_id[item_id]
        matches = provenance.get((source.id, src.id), [])
        existing = matches[0] if matches else None
        if mode == "copy":
            if existing is not None:
                raise ValueError(f"「{src.title}」已经导入过，请选择覆盖或跳过")
            copies.append(_copied_record(source.id, src))
        elif existing is None:
            raise ValueError(f"「{src.title}」还没有可覆盖的副本")
        else:
            overwrites.append((existing, src))

    if len(destination.archive_records) + len(copies) > 2000:
        raise ValueError("导入后将超过 2000 条档案上限")

    now = utcnow()
    for existing, src in overwrites:
        existing.body = src.body
        existing.kind_data = dict(src.kind_data)
        existing.revision += 1
        existing.updated_at = now
        existing.copied_from_revision = src.revision
    destination.archive_records.extend(copies)


def _copied_record(source_world_id: str, src: ArchiveRecord) -> ArchiveRecord:
    return ArchiveRecord(
        kind="biology",
        subtype=src.subtype,
        title=src.title,
        aliases=list(src.aliases),
        tags=list(src.tags),
        summary=src.summary,
        body=src.body,
        visibility=src.visibility,
        kind_data=dict(src.kind_data),
        copied_from_world_id=source_world_id,
        copied_from_archive_id=src.id,
        copied_from_revision=src.revision,
    )
