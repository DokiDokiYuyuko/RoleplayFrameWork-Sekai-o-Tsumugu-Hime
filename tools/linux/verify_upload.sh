#!/usr/bin/env bash
set -euo pipefail
P="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$P/.venv/bin/python" "$P/tools/maintenance/verify_modelscope.py" "$@"
