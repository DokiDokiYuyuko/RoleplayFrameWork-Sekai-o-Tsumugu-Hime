#!/usr/bin/env bash
set -euo pipefail
P="$(cd "$(dirname "$0")" && pwd)"
exec bash "$P/tools/linux/run.sh" "$@"
