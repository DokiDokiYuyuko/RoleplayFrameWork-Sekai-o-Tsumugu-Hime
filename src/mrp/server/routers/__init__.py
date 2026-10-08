"""路由清单：`create_app()` 按此顺序 include（顺序仅影响同形路径的优先级，
当前各路由路径/方法两两不冲突，顺序与原 app.py 无行为差异）。"""
from __future__ import annotations

from . import (
    bundle,
    characters,
    lorebooks,
    memory,
    messages,
    conversation_runs,
    sessions,
    scenarios,
    pinned_facts,
    settings,
    tts,
    system,
    lan,
    workshop,
    worldlines,
    worlds,
    groups,
    asset_imports,
    simple_chats,
    prompt_presets,
    lorebook_generation,
    card_discovery,
    card_inspiration,
    stage3,
    world_runtime,
    world_organize,
    story_views,
)

ALL_ROUTERS = [
    story_views.router,
    world_runtime.router,
    world_organize.router,
    stage3.router,
    system.router,      # health
    lan.router,         # opt-in LAN pairing and local controls
    sessions.router,    # 会话 CRUD/设置/存档/场景/辅助/导演/事件流/检查器/成本
    messages.router,    # 发送/流式回合/编辑/删除/swipe/变体/续写/重生成
    conversation_runs.router,
    characters.router,  # 角色导入/CRUD/头像/导出
    lorebooks.router,   # 世界书导入/CRUD/条目/导出
    memory.router,      # 记忆查看/检索/编辑/固化
    workshop.router,    # 工坊生成/润色
    bundle.router,      # 迁移包导入/导出
    scenarios.router,   # 可复用剧情场景预设
    pinned_facts.router,  # 会话固定剧情信息
    settings.router,    # 全局设置
    tts.router,         # 本地语音合成与音色
    worldlines.router,  # 故事/分支/事件
    worlds.router,      # 世界档案与世界书归属
    groups.router,      # 会话群体参与者与群体回应
    asset_imports.router,  # 智能设定导入草稿
    simple_chats.router,  # 独立简单聊天
    prompt_presets.router,  # 完整提示词方案
    lorebook_generation.router,  # DSH Agent 世界书生成与审阅
    card_discovery.router,  # 外部角色卡站点搜索与预览
    card_inspiration.router,  # 角色灵感任务、草稿与确认保存
]
