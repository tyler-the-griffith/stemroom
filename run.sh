#!/usr/bin/env bash
# Start Stemroom on every interface so phones on the same Wi-Fi can join.
#   STEMROOM_PORT=8765       port (default 8765)
#   STEMROOM_DEVICE=cpu      keep the GPU free (e.g. while the Windows VM needs it)
set -euo pipefail
cd "$(dirname "$0")"
PORT="${STEMROOM_PORT:-8765}"
exec .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port "$PORT" "$@"
