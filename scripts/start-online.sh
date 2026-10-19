#!/usr/bin/env bash
# Keep this process running while the online room is available.
set -euo pipefail
cd "$(dirname "$0")/.."
if (( $# == 0 )); then
  echo 'Usage: ./scripts/start-online.sh --public | owner@example.com [guest@example.com ...]' >&2
  exit 2
fi
client="${CLOUDFLARED:-$PWD/data/tools/cloudflared}"
if [[ ! -x "$client" ]]; then client="$(command -v cloudflared || true)"; fi
if [[ -z "$client" || ! -x "$client" ]]; then
  echo 'Install cloudflared from https://developers.cloudflare.com/tunnel/downloads/ first.' >&2
  exit 1
fi
args=()
if [[ "$1" == '--public' ]]; then
  if (( $# != 1 )); then
    echo 'Use --public alone, or supply allowed email addresses instead.' >&2
    exit 2
  fi
  echo 'Public room: anyone with the URL has full library and player control.' >&2
  shift
fi
for email in "$@"; do
  if [[ "$email" != *@* || "$email" == *'*'* ]]; then
    echo 'Supply individual allowed email addresses; wildcard access is disabled by this helper.' >&2
    exit 2
  fi
  args+=(--allowed-mail "$email")
done
port="${STEMROOM_PORT:-8765}"
curl --fail --silent --output /dev/null "http://127.0.0.1:$port/api/info" || {
  echo 'Start Stemroom with ./run.sh before opening its tunnel.' >&2
  exit 1
}
exec "$client" tunnel --no-autoupdate --url "http://127.0.0.1:$port" "${args[@]}"
