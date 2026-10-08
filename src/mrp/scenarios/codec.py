"""Portable .mrpscenario ZIP codec. Archives are read in memory; no paths are extracted."""
from __future__ import annotations

import io
import hashlib
import json
import zipfile

from .schema import ScenarioPackage, legacy_package_id

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_SCENARIO_JSON_BYTES = 10 * 1024 * 1024
_MANIFEST_VERSION = 1
_SCENARIO_ENTRY = "scenario.json"
_MANIFEST_ENTRY = "manifest.json"


def encode_package(package: ScenarioPackage) -> bytes:
    payload = package.model_dump_json(indent=2).encode("utf-8")
    manifest = {
        "format": "mrp.scenario",
        "format_version": _MANIFEST_VERSION,
        "package_id": package.package_id,
        "revision": package.revision,
        "title": package.title,
        "entry": _SCENARIO_ENTRY,
        "files": {
            _SCENARIO_ENTRY: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
        "required_components": sorted(
            name for name, component in package.components.items() if component.required
        ),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_MANIFEST_ENTRY, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        archive.writestr(_SCENARIO_ENTRY, payload)
    return out.getvalue()


def decode_package(raw: bytes) -> ScenarioPackage:
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("场景包为空或超过 50MB")
    manifest = None
    if zipfile.is_zipfile(io.BytesIO(raw)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [item.filename for item in archive.infolist() if not item.is_dir()]
                if len(names) != len(set(names)):
                    raise ValueError("场景包包含重复文件名")
                unsupported = set(names) - {_MANIFEST_ENTRY, _SCENARIO_ENTRY}
                if unsupported:
                    raise ValueError("场景包包含当前版本无法识别的文件：" + "、".join(sorted(unsupported)[:5]))
                try:
                    info = archive.getinfo(_SCENARIO_ENTRY)
                except KeyError as exc:
                    raise ValueError("场景包缺少 scenario.json") from exc
                if info.file_size > MAX_SCENARIO_JSON_BYTES:
                    raise ValueError("场景设定超过 10MB")
                payload = archive.read(info)
                manifest = None
                try:
                    manifest_info = archive.getinfo(_MANIFEST_ENTRY)
                except KeyError:
                    # Historical packages contained only scenario.json.
                    pass
                else:
                    if manifest_info.file_size > 1024 * 1024:
                        raise ValueError("场景包清单超过 1MB")
                    try:
                        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError("场景包清单不是有效的 UTF-8 JSON") from exc
                    if not isinstance(manifest, dict):
                        raise ValueError("场景包清单格式无效")
                    if manifest.get("format") != "mrp.scenario":
                        raise ValueError("场景包清单类型不受支持")
                    if manifest.get("format_version") != _MANIFEST_VERSION:
                        raise ValueError("场景包清单格式版本不受支持")
                    if not isinstance(manifest.get("package_id"), str) or not manifest["package_id"].strip():
                        raise ValueError("场景包清单缺少有效的包身份")
                    if not isinstance(manifest.get("revision"), int) or isinstance(manifest.get("revision"), bool) or manifest["revision"] < 1:
                        raise ValueError("场景包清单缺少有效的修订号")
                    if not isinstance(manifest.get("title"), str) or not manifest["title"].strip():
                        raise ValueError("场景包清单缺少有效的标题")
                    if manifest.get("entry") != _SCENARIO_ENTRY:
                        raise ValueError("场景包清单入口无效")
                    files = manifest.get("files")
                    file_record = files.get(_SCENARIO_ENTRY) if isinstance(files, dict) else None
                    if not isinstance(file_record, dict):
                        raise ValueError("场景包清单缺少正文校验信息")
                    if file_record.get("size") != len(payload):
                        raise ValueError("场景包正文长度与清单不符")
                    digest = file_record.get("sha256")
                    if not isinstance(digest, str) or digest != hashlib.sha256(payload).hexdigest():
                        raise ValueError("场景包正文校验失败，文件可能已损坏")
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise ValueError("场景包压缩文件损坏或无法读取") from exc
    else:
        if len(raw) > MAX_SCENARIO_JSON_BYTES:
            raise ValueError("场景设定超过 10MB")
        payload = raw
    try:
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("场景包正文顶层必须是对象")
        if manifest is not None:
            package_id = manifest.get("package_id")
            revision = manifest.get("revision")
            if data.get("package_id") not in (None, package_id):
                raise ValueError("清单与正文的包身份不一致")
            if data.get("revision") not in (None, revision):
                raise ValueError("清单与正文的修订号不一致")
            if not data.get("package_id"):
                data["package_id"] = package_id
            if data.get("revision") is None:
                data["revision"] = revision
        elif not data.get("package_id"):
            # Stable identity for legacy packages lacking package metadata.
            source_id = str(data.get("id") or hashlib.sha256(payload).hexdigest())
            data["package_id"] = legacy_package_id(source_id)
        package = ScenarioPackage.model_validate(data)
        if manifest is not None:
            if package.title != manifest["title"]:
                raise ValueError("清单与正文的预设标题不一致")
            declared_required = manifest.get("required_components", [])
            if not isinstance(declared_required, list) or any(not isinstance(item, str) for item in declared_required):
                raise ValueError("场景包清单中的功能模块列表无效")
            actual_required = sorted(
                name for name, component in package.components.items() if component.required
            )
            if sorted(declared_required) != actual_required:
                raise ValueError("清单与正文的必需功能模块不一致")
        return package
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("场景包不是有效的 UTF-8 JSON") from exc
