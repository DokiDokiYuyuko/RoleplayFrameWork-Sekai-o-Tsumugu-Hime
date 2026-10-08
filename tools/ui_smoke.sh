#!/usr/bin/env bash
set -euo pipefail
P="$(cd "$(dirname "$0")" && pwd)"
exec bash "$P/linux/ui_smoke.sh" "$@"
