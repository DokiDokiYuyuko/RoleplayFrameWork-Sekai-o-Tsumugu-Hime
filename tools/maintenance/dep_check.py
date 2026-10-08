#!/usr/bin/env python3
"""依赖许可检查（ADR-0003 红线的自动化检查）。

读 pyproject.toml 依赖 → 查 PyPI metadata LICENSE → 与白名单比对。
在引入新依赖或升级后运行。只读、不修改任何文件（结果打印 + 退出码）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from workspace_paths import PROJECT_ROOT

ALLOWED = {"MIT", "MIT-CMU", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "BSD-3", "ISC", "MPL-2.0", "Unlicense", "CC0-1.0"}
# 已登记在 third_party/README.md 的深seek系（PyPI metadata 标 MIT）
KNOWN_GOOD = {"deepseek-harness-sdk", "deepseek-harness-runtime-bin"}


def extract_deps(pyproject_path: str) -> list[str]:
    text = open(pyproject_path, encoding="utf-8").read()
    m = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.M | re.S)
    if not m:
        return []
    # 取每行的包名（去掉版本约束与注释）
    names = []
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~\[; ]", line)[0].strip("\"'")
        if name:
            names.append(name)
    return names


def pypi_license(pkg: str) -> str:
    url = f"https://pypi.org/pypi/{pkg}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "mrp-dep-check"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        info = json.load(resp)["info"]
    lic = (info.get("license_expression") or info.get("license") or "").strip()
    if not lic:
        classifiers = [c for c in (info.get("classifiers") or []) if "License" in c]
        lic = classifiers[0].split("::")[-1].strip() if classifiers else ""
    return lic


def main() -> int:
    pyproject = sys.argv[1] if len(sys.argv) > 1 else str(PROJECT_ROOT / "pyproject.toml")
    deps = extract_deps(pyproject)
    if not deps:
        print("未找到依赖（pyproject dependencies 为空？）")
        return 1
    ok = True
    print(f"{'包名':<40} {'许可':<24} 结果")
    for pkg in deps:
        try:
            lic = pypi_license(pkg)
        except Exception as exc:  # noqa: BLE001
            print(f"{pkg:<40} {'查询失败':<24} WARN — {exc}")
            continue
        normalized = lic.split("\n")[0][:40]
        passed = any(a in lic for a in ALLOWED) or pkg in KNOWN_GOOD
        if not passed:
            ok = False
        print(f"{pkg:<40} {normalized:<24} {'PASS' if passed else 'FAIL(非白名单许可!)'}")
    print("\n结论:", "全部通过" if ok else "存在非白名单许可——按 ADR-0003 人工审查后再引入")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
