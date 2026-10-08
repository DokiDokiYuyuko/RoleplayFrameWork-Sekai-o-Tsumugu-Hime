"""每角色 dsh profile 生成与引擎目录管理。

机制全部经 M0 spike 验证（spikes/m0-dsh-sdk/common.py 晋升，证据见
third_party/README.md「M0 实测约束」）：
- DSH_HOME 必须在本地磁盘（cephfs 文件锁超时），默认 ~/.cache/mrp-engines
- profile 基于 sdk-minimal bundle，补丁禁用 persistent bash/pwsh（零工具）
- persona 经 DSH_SYSTEM_PROMPT 环境变量进 system-prompt 行的 personaPrefix
  （env 传递，天然无 YAML 注入风险；patch 文件本身是静态的）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mrp.shared.models import CharacterCard

PROFILE_BUNDLE = "@deepseek-ai/dsh-sdk-minimal"
PROFILE_NAME = "main"

_PATCH_YML = (
    "# mrp per-character patch: pure dialogue character, zero tools\n"
    "- id: persistent-bash\n"
    "  disabled: true\n"
    "- id: persistent-pwsh\n"
    "  disabled: true\n"
)


def engines_root() -> Path:
    """每角色 DSH_HOME 根目录（本地磁盘；MRP_ENGINES_ROOT 可覆盖）。"""
    root = os.environ.get("MRP_ENGINES_ROOT") or str(Path.home() / ".cache" / "mrp-engines")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def character_home(character_id: str, root: Path | None = None) -> Path:
    """角色独立 DSH_HOME：<root>/<character_id>/。"""
    home = (root or engines_root()) / character_id
    home.mkdir(parents=True, exist_ok=True)
    return home


def write_character_profile(dsh_home: Path, profile_name: str = PROFILE_NAME) -> Path:
    """在 dsh_home 下生成自定义 profile（4 个小文件，手写即可，无需 subprocess）。"""
    pdir = dsh_home / "profiles" / profile_name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.json").write_text(
        json.dumps(
            {
                "name": f"dsh-profile-{profile_name}",
                "private": True,
                "dependencies": {},
                "dsh": {
                    "profile": {"bundles": [PROFILE_BUNDLE], "patchReload": "startup"}
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pdir / "cordis.yml").write_text("[]\n", encoding="utf-8")
    (pdir / "pnpm-workspace.yaml").write_text(
        "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n",
        encoding="utf-8",
    )
    (pdir / "cordis.patch.yml").write_text(_PATCH_YML, encoding="utf-8")
    return pdir


def write_lorebook_agent_profile(dsh_home: Path, skill_dir: Path, profile_name: str = "main") -> Path:
    """Create the isolated DSH profile used by the offline lorebook Agent."""
    pdir = dsh_home / "profiles" / profile_name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.json").write_text(
        json.dumps({
            "name": f"mrp-lorebook-agent-{profile_name}",
            "private": True,
            "dependencies": {},
            "dsh": {"profile": {"bundles": [PROFILE_BUNDLE], "patchReload": "startup"}},
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (pdir / "cordis.yml").write_text("[]\n", encoding="utf-8")
    (pdir / "pnpm-workspace.yaml").write_text(
        "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n", encoding="utf-8"
    )
    skill_path = json.dumps(str(skill_dir.resolve()), ensure_ascii=False)
    patch = f'''# Isolated DSH lorebook Agent: project Skill + read-only task tools.
- id: persistent-bash
  disabled: true
- id: persistent-pwsh
  disabled: true
- insert:
    - id: skill
      name: '@deepseek-ai/dsh-skill'
    - id: skill-filesystem
      name: '@deepseek-ai/dsh-skill-filesystem'
      config:
        includeDefaultRoots: false
        customSkillDirs:
          - {skill_path}
    - id: tool-skill
      name: '@deepseek-ai/dsh-tool-skill'
    - id: mcp-client
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: mrp-lorebook
        transport: stdio
        command: !!js process.env.MRP_LOREBOOK_MCP_PYTHON
        args:
          - !!js process.env.MRP_LOREBOOK_MCP_SERVER
        env:
          MRP_LOREBOOK_TASK_FILE: !!js process.env.MRP_LOREBOOK_TASK_FILE
'''
    (pdir / "cordis.patch.yml").write_text(patch, encoding="utf-8")
    return pdir


def write_card_agent_profile(dsh_home: Path, skill_dir: Path, profile_name: str = "main") -> Path:
    """Create a dedicated card ideation profile with only read-only task MCP tools."""
    pdir = dsh_home / "profiles" / profile_name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "package.json").write_text(json.dumps({
        "name": f"mrp-card-agent-{profile_name}", "private": True, "dependencies": {},
        "dsh": {"profile": {"bundles": [PROFILE_BUNDLE], "patchReload": "startup"}},
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    (pdir / "cordis.yml").write_text("[]\n", encoding="utf-8")
    (pdir / "pnpm-workspace.yaml").write_text(
        "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n", encoding="utf-8"
    )
    skill_path = json.dumps(str(skill_dir.resolve()), ensure_ascii=False)
    patch = f'''# Isolated character ideation Agent: project Skill + read-only task tools.
- id: persistent-bash
  disabled: true
- id: persistent-pwsh
  disabled: true
- insert:
    - id: skill
      name: '@deepseek-ai/dsh-skill'
    - id: skill-filesystem
      name: '@deepseek-ai/dsh-skill-filesystem'
      config:
        includeDefaultRoots: false
        customSkillDirs:
          - {skill_path}
    - id: tool-skill
      name: '@deepseek-ai/dsh-tool-skill'
    - id: mcp-client
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: mrp-card-inspiration
        transport: stdio
        command: !!js process.env.MRP_CARD_MCP_PYTHON
        args:
          - !!js process.env.MRP_CARD_MCP_SERVER
        env:
          MRP_CARD_MCP_TASK_FILE: !!js process.env.MRP_CARD_MCP_TASK_FILE
'''
    (pdir / "cordis.patch.yml").write_text(patch, encoding="utf-8")
    return pdir

