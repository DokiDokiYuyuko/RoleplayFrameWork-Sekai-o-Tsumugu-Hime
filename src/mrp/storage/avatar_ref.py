"""角色头像以 `characters/<id>/avatar.png` 为准。

展示接口按这个文件读。导出也读这个文件，卡片里的 `avatar_path` 还空着也算数。
库加载时如果文件在、字段又不是这条相对路径，只改 `card.avatar_path`，不动 revision 和 updated_at。
标准文件不在时，不把已写下的路径清掉。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mrp.shared.models import Character

from .atomic import write_json_atomic
from .paths import is_safe_name

AVATAR_FILENAME = "avatar.png"


class AvatarLoad(str, Enum):
    FOUND = "found"
    ABSENT = "absent"
    INVALID = "invalid"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class BundleAvatar:
    status: AvatarLoad
    data: bytes | None = None


def canonical_avatar_rel(character_id: str) -> str | None:
    if not is_safe_name(character_id):
        return None
    return f"{character_id}/{AVATAR_FILENAME}"


def canonical_avatar_file(characters_dir: Path, character_id: str) -> Path | None:
    if canonical_avatar_rel(character_id) is None:
        return None
    path = characters_dir / character_id / AVATAR_FILENAME
    return path if path.is_file() else None


def read_display_avatar(characters_dir: Path, character_id: str) -> bytes | None:
    """PNG 卡导出用。只认标准文件，不跟着卡片里另写的路径走。"""
    path = canonical_avatar_file(characters_dir, character_id)
    if path is None:
        return None
    return path.read_bytes()


def load_bundle_avatar(
    characters_dir: Path, character_id: str, avatar_path: str | None
) -> BundleAvatar:
    """迁移包用。标准文件优先；没有时才看卡片里的相对路径。

    路径逃出角色库 → INVALID。标准文件在但读失败 → UNREADABLE。
    记录的路径指向的文件不在 → ABSENT，调用方跳过即可。
    """
    canonical = canonical_avatar_file(characters_dir, character_id)
    if canonical is not None:
        try:
            return BundleAvatar(AvatarLoad.FOUND, canonical.read_bytes())
        except OSError:
            return BundleAvatar(AvatarLoad.UNREADABLE)
    if not avatar_path:
        return BundleAvatar(AvatarLoad.ABSENT)
    root = Path(characters_dir).resolve()
    try:
        candidate = (root / avatar_path).resolve()
    except (OSError, ValueError):
        return BundleAvatar(AvatarLoad.INVALID)
    if not candidate.is_relative_to(root):
        return BundleAvatar(AvatarLoad.INVALID)
    if not candidate.is_file():
        return BundleAvatar(AvatarLoad.ABSENT)
    try:
        return BundleAvatar(AvatarLoad.FOUND, candidate.read_bytes())
    except OSError:
        return BundleAvatar(AvatarLoad.UNREADABLE)


def bind_library_avatar(character: Character, characters_dir: Path) -> bool:
    """标准文件在、且字段还不是它的相对路径时，改内存里的字段。

    不改 revision / updated_at。标准文件不在时保持原字段，包括一条已经失效的路径。
    返回值表示这次是否改了字段。
    """
    rel = canonical_avatar_rel(character.id)
    if rel is None or not (characters_dir / character.id / AVATAR_FILENAME).is_file():
        return False
    if character.card.avatar_path == rel:
        return False
    character.card.avatar_path = rel
    return True


def write_bound_avatar_path(json_path: Path, avatar_path: str) -> None:
    """只把磁盘 JSON 的 `card.avatar_path` 改成给定值，保留键顺序。

    不用模型整表重写：那会重排字段。字段已经是这个值时不写盘。
    """
    data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    card = data.get("card") if isinstance(data, dict) else None
    if not isinstance(card, dict) or card.get("avatar_path") == avatar_path:
        return
    card["avatar_path"] = avatar_path
    write_json_atomic(json_path, data, indent=None, separators=(",", ":"))
