from __future__ import annotations

from typing import Iterable

from .schemas import CardSource


class CardSourceRegistry:
    """Small registration boundary for independently implemented source connectors."""

    def __init__(self, sources: Iterable[CardSource] = ()) -> None:
        self.sources: dict[str, CardSource] = {}
        for source in sources:
            self.register(source)

    def register(self, source: CardSource) -> None:
        source_id = source.source_id.strip()
        if not source_id or source_id in self.sources:
            raise ValueError("角色卡站点 ID 为空或重复")
        capabilities = source.capabilities()
        if capabilities.source_id != source_id:
            raise ValueError("连接器 ID 与能力声明不一致")
        if capabilities.full_card and not callable(getattr(source, "full_card", None)):
            raise ValueError("声明支持完整卡的连接器必须实现 full_card")
        if not callable(getattr(source, "search", None)):
            raise ValueError("角色卡站点连接器必须实现 search")
        self.sources[source_id] = source

