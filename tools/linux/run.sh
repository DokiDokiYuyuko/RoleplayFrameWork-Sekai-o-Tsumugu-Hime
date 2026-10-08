#!/usr/bin/env bash
# mrp 本地启动脚本（R7.1：一条命令起服务）
# 用法: ./run.sh [--fake]   --fake 用 FakeEngine（无需 API 密钥，冒烟/开发用）
set -euo pipefail
P="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$P"

# 依赖环境
PY_FINGERPRINT="$(python3 -c 'import hashlib; from pathlib import Path; print(":".join(hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ("uv.lock","requirements-lock.txt","pyproject.toml")))')"
if [ ! -x "$P/.venv/bin/python" ] || [ ! -f "$P/.venv/.sekai-dependencies-complete" ] || [ "$(cat "$P/.venv/.sekai-dependencies-complete" 2>/dev/null || true)" != "$PY_FINGERPRINT" ]; then
  echo "[setup] 创建虚拟环境并安装依赖（首次约 2 分钟）..."
  if command -v uv >/dev/null 2>&1; then
    uv sync --frozen --no-dev
  else
    python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
    if [ ! -x "$P/.venv/bin/python" ]; then python3 -m venv "$P/.venv"; fi
    "$P/.venv/bin/python" -m ensurepip --upgrade
    "$P/.venv/bin/python" -m pip install --require-hashes -r requirements-lock.txt
    "$P/.venv/bin/python" -m pip install --no-deps -e "$P"
  fi
  printf '%s\n' "$PY_FINGERPRINT" > "$P/.venv/.sekai-dependencies-complete"
fi

PY="$P/.venv/bin/python"

# 前端：首次构建（若未构建过且 src/web 存在）
if [ -d "$P/src/web" ] && [ ! -d "$P/src/web/dist" ] && [ -f "$P/src/web/package.json" ]; then
  echo "[setup] 构建前端（首次约 1-2 分钟）..."
  (cd "$P/src/web" && npm ci && npm run build)
fi

# 环境变量
[ -f "$P/.env" ] && set -a && . "$P/.env" && set +a
export MRP_DATA_ROOT="${MRP_DATA_ROOT:-$(dirname "$P")/data}"
if [ "${1:-}" = "--fake" ]; then
  export MRP_FAKE_ENGINE=1
  echo "[run] FAKE 模式（无需密钥）"
fi

PORT="${MRP_PORT:-8000}"
# The regular launcher always stays loopback-only. LAN binding is available
# only through start_lan.ps1 and cannot be enabled by stale .env values.
export MRP_HOST=127.0.0.1 MRP_PORT="$PORT" MRP_LAN_MODE=0
unset MRP_LAN_ACCESS_CODE
echo "[run] 后端 http://127.0.0.1:$PORT ｜ 前端与API文档同端口（/ 与 /docs）"
echo "[run] 手机访问请使用 Windows 上的 start_lan.bat 显式开启局域网入口"
cd "$P"   # 程序化入口按模块运行（无需 --app-dir；包已 editable 安装）
exec "$PY" -m mrp.server.main
