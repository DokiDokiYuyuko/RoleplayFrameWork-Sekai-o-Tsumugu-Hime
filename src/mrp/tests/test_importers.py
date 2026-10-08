"""importers 单测：角色卡（PNG tEXt / CCv1-3）与世界书（ST / embedded / Risu / 自动探测）。"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from mrp.importers.character_card import (
    import_card_json,
    import_card_png,
    save_avatar_png,
)
from mrp.importers.lorebook import (
    import_lorebook,
    import_lorebook_embedded,
    import_lorebook_risu,
    import_lorebook_st,
)


# ---------- 工具 ----------


def _b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _png_with_texts(texts: dict[str, str]) -> bytes:
    img = Image.new("RGB", (4, 4))
    info = PngImagePlugin.PngInfo()
    for k, v in texts.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


# ---------- 角色卡：PNG ----------


def test_png_chara_roundtrip():
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "测试甲",
            "description": "合成角色甲简介",
            "first_mes": "你好。",
            "tags": ["campus"],
        },
    }
    png = _png_with_texts({"chara": _b64(card)})
    imported = import_card_png(png)
    assert imported.name == "测试甲"
    assert imported.description == "合成角色甲简介"
    assert imported.first_mes == "你好。"
    assert imported.tags == ["campus"]
    assert imported.source_format == "ccv2"
    assert imported.spec == "chara_card_v2"


def test_png_ccv3_wins_over_chara():
    ccv3 = {"spec": "chara_card_v3", "data": {"name": "V3角色"}}
    chara = {"spec": "chara_card_v2", "data": {"name": "旧V2角色"}}
    png = _png_with_texts({"chara": _b64(chara), "ccv3": _b64(ccv3)})
    assert import_card_png(png).name == "V3角色"
    assert import_card_png(png).source_format == "ccv3"


def test_png_no_card_raises():
    with pytest.raises(ValueError):
        import_card_png(_png_with_texts({"comment": "no card here"}))


# ---------- 角色卡：三种 JSON 格式 ----------


def test_import_ccv1_flat():
    obj = {
        "name": "老卡",
        "description": "平铺格式",
        "personality": "温柔",
        "scenario": "咖啡馆",
        "first_mes": "欢迎光临。",
        "mes_example": "<START>",
        "unknown_v1_field": 42,
    }
    card = import_card_json(obj)
    assert card.source_format == "ccv1"
    assert card.name == "老卡"
    assert card.personality == "温柔"
    assert card.scenario == "咖啡馆"
    assert card.first_mes == "欢迎光临。"
    assert card.mes_example == "<START>"
    assert card.extensions["unknown_v1_field"] == 42


def test_import_flat_card_declaring_v2_spec():
    """平铺卡声明 spec=chara_card_v2（内部模型/工坊产物/表单新建的形态）——
    data 包装缺失时按平铺读，不得读成空卡（2026-09-25 修复："未命名角色"事故）。"""
    obj = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "name": "表单新建角色",
        "description": "由表单创建",
        "first_mes": "（点头）我是新来的。",
        "tags": [],
        "extensions": {},
    }
    card = import_card_json(obj)
    assert card.source_format == "ccv2"
    assert card.name == "表单新建角色"
    assert card.description == "由表单创建"
    assert card.first_mes == "（点头）我是新来的。"


def test_import_ccv2_wrapped():
    obj = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "V2卡",
            "description": "data 包装",
            "alternate_greetings": ["备选开场"],
            "system_prompt": "你是{{char}}。",
            "post_history_instructions": "保持简短",
            "creator": "作者A",
            "character_version": "1.2",
            "tags": ["fantasy"],
            "extensions": {"depth_prompt": {"depth": 4}},
            "custom_field": "x",
        },
        "top_extra": "y",
    }
    card = import_card_json(obj)
    assert card.source_format == "ccv2"
    assert card.spec == "chara_card_v2"
    assert card.name == "V2卡"
    assert card.alternate_greetings == ["备选开场"]
    assert card.system_prompt == "你是{{char}}。"
    assert card.post_history_instructions == "保持简短"
    assert card.creator == "作者A"
    assert card.character_version == "1.2"
    assert card.tags == ["fantasy"]
    assert card.extensions["depth_prompt"] == {"depth": 4}  # 自带 extensions 保留
    assert card.extensions["custom_field"] == "x"  # data 层未知字段
    assert card.extensions["top_extra"] == "y"  # 头部层未知字段


def test_import_ccv3_superset():
    obj = {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {"name": "V3卡", "description": "V2 超集", "creator_notes": "备注"},
    }
    card = import_card_json(obj)
    assert card.source_format == "ccv3"
    assert card.spec == "chara_card_v3"
    assert card.spec_version == "3.0"
    assert card.creator_notes == "备注"


def test_import_card_missing_fields_no_crash():
    card = import_card_json({})
    assert card.source_format == "ccv1"
    assert card.name  # 缺 name 给默认名，不炸
    assert card.description == ""
    assert card.alternate_greetings == []
    assert card.system_prompt is None
    card2 = import_card_json({"data": {"name": "只有data"}})
    assert card2.source_format == "ccv2"
    assert card2.name == "只有data"


# ---------- 角色卡：头像保存 ----------


def test_save_avatar_png(tmp_path: Path):
    png = _png_with_texts({})
    rel = save_avatar_png(png, "char-abc123", tmp_path)
    assert rel == "char-abc123/avatar.png"
    saved = tmp_path / "char-abc123" / "avatar.png"
    assert saved.read_bytes() == png
    with pytest.raises(ValueError):
        save_avatar_png(png, "../evil", tmp_path)


# ---------- 世界书：ST ----------


def _st_book() -> dict:
    return {
        "name": "校园设定",
        "description": "测试书",
        "entries": {
            "0": {
                "uid": 0,
                "key": ["图书馆", "library"],
                "keysecondary": ["安静"],
                "content": "图书馆在东侧。",
                "comment": "地点",
                "disable": False,
                "constant": False,
                "selective": True,
                "selective_logic": 3,
                "order": 50,
                "depth": 4,
                "probability": 80,
                "useProbability": True,
                "position": 0,
                "sticky": 10,
                "excludeRecursion": True,
            },
            "1": {
                "uid": 1,
                "key": ["回忆"],
                "content": "插入在深度处。",
                "disable": True,  # 反向 → enabled=False
                "position": 4,
                "depth": 8,
                "cooldown": 5,
            },
            "2": {
                "uid": 2,
                "key": ["耳语"],
                "content": "靠近消息插入。",
                "position": 7,
                "preventRecursion": True,
            },
        },
    }


def test_st_lorebook_basic_mapping():
    book = import_lorebook_st(_st_book())
    assert book.source_format == "st"
    assert book.name == "校园设定"
    assert len(book.entries) == 3
    e0 = book.entries[0]
    assert e0.uid == 0
    assert e0.keys == ["图书馆", "library"]
    assert e0.secondary_keys == ["安静"]
    assert e0.content == "图书馆在东侧。"
    assert e0.enabled is False  # sticky 时效未执行，安全默认停用并报告
    assert "sticky" in e0.extensions["mrp.import_compatibility"]["unexecuted_rules"]
    assert e0.selective is True
    assert e0.selective_logic == 3
    assert e0.order == 50
    assert e0.anchor == "system"  # position 0
    assert e0.probability == 80
    # ST 专有字段进 extensions
    assert e0.extensions["sticky"] == 10
    assert e0.extensions["excludeRecursion"] is True
    assert e0.extensions["position"] == 0


def test_st_disable_semantics_and_anchors():
    book = import_lorebook_st(_st_book())
    e1 = book.entries[1]
    assert e1.enabled is False  # disable=True → enabled=False
    assert e1.anchor == "at_depth"  # position 4
    assert e1.depth == 8
    assert e1.extensions["cooldown"] == 5
    e2 = book.entries[2]
    assert e2.anchor == "near"  # position 7
    assert e2.extensions["preventRecursion"] is True


def test_st_use_probability_false_defaults_100():
    obj = {"entries": {"0": {"uid": 0, "key": ["x"], "probability": 30, "useProbability": False}}}
    assert import_lorebook_st(obj).entries[0].probability == 100


# ---------- 世界书：embedded ----------


def test_embedded_lorebook():
    obj = {
        "name": "内嵌书",
        "scan_depth": 4,
        "token_budget": 2048,
        "recursive_scanning": False,
        "entries": [
            {
                "id": 7,
                "keys": ["魔法"],
                "secondary_keys": ["禁忌"],
                "content": "魔法被禁止。",
                "enabled": True,
                "insertion_order": 20,
                "constant": True,
                "position": "before_char",
                "selective": True,
                "priority": 3,
                "case_sensitive": False,
            },
            {"id": 8, "keys": ["遗迹"], "content": "古城遗迹。", "enabled": False},
        ],
    }
    book = import_lorebook_embedded(obj)
    assert book.source_format == "embedded"
    assert book.scan_depth == 4
    assert book.token_budget == 2048
    assert book.recursive_scanning is False
    e = book.entries[0]
    assert e.uid == 7  # id → uid
    assert e.keys == ["魔法"]
    assert e.secondary_keys == ["禁忌"]
    assert e.enabled is True  # 正向直取
    assert e.constant is True
    assert e.order == 20  # insertion_order → order
    assert e.anchor == "system"  # before_char/after_char 都归 system
    assert e.extensions["priority"] == 3
    assert e.extensions["case_sensitive"] is False
    assert book.entries[1].enabled is False


# ---------- 世界书：Risu ----------


def test_risu_lorebook():
    obj = {
        "name": "Risu书",
        "entries": [
            {
                "id": 1,
                "key": "剑术, 剑法 ,/bl+ade/",
                "secondkey": "师父",
                "content": "主角会剑术。",
                "comment": "背景",
                "insertorder": 30,
                "alwaysActive": True,
                "selective": True,
                "probability": 60,
                "mode": "constant",  # 未映射字段
            }
        ],
    }
    book = import_lorebook_risu(obj)
    assert book.source_format == "risu"
    e = book.entries[0]
    assert e.keys == ["剑术", "剑法", "/bl+ade/"]  # 逗号拆分、去空白、丢空串
    assert e.secondary_keys == ["师父"]
    assert e.order == 30  # insertorder → order
    assert e.constant is True  # alwaysActive → constant
    assert e.probability == 60
    assert e.extensions["mode"] == "constant"


def test_risu_empty_keys_dropped():
    obj = {"entries": [{"key": " , ,,", "content": "x"}]}
    assert import_lorebook_risu(obj).entries[0].keys == []


# ---------- 世界书：自动探测 ----------


def test_import_lorebook_autodetect():
    st = import_lorebook(_st_book())
    assert st.source_format == "st"

    embedded = import_lorebook({"entries": [{"keys": ["k"], "content": "x"}]})
    assert embedded.source_format == "embedded"

    risu = import_lorebook({"entries": [{"insertorder": 1, "alwaysActive": True, "content": "x"}]})
    assert risu.source_format == "risu"


# ---------- 全空 / 畸形输入 ----------


def test_lorebook_malformed_inputs_tolerated():
    assert import_lorebook({}).entries == []
    assert import_lorebook({"entries": None}).entries == []
    assert import_lorebook({"entries": "garbage"}).entries == []
    assert import_lorebook_st({"entries": {"0": "not a dict", "1": {"uid": 1, "content": "ok"}}}).entries[0].content == "ok"
    # 条目缺字段 → 全默认值
    book = import_lorebook_st({"entries": {"0": {}}})
    e = book.entries[0]
    assert e.uid == 0  # dict 键 fallback
    assert e.enabled is True
    assert e.order == 100
    assert e.anchor == "system"
    assert e.keys == []
    # 类型错误容错
    book2 = import_lorebook_st({"entries": {"x": {"uid": "bad", "order": "bad", "key": "单字符串"}}})
    assert book2.entries[0].order == 100
    assert book2.entries[0].keys == ["单字符串"]
    assert import_lorebook_embedded({"entries": [None, 5, {"keys": ["k"], "content": "y"}]}).entries[0].content == "y"


def test_card_json_malformed_no_crash():
    # 非字符串字段给默认值，不炸
    card = import_card_json({"name": 123, "tags": "not-a-list", "extensions": "bad"})
    assert card.name == "未命名角色"
    assert card.tags == ["not-a-list"]  # 单字符串宽容包成 list
    assert card.extensions == {}
