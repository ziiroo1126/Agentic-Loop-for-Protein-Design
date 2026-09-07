#!/usr/bin/env bash
# Install only the CPU core; uv reuses its local cache before downloading.
set -euo pipefail
alpd_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo 'ALPD needs uv and Python 3.12 or 3.13. See docs/QUICKSTART.md.' >&2
  exit 2
fi
cd "$alpd_root/interaction-design-mvp"
# Scope direct networking to this installer and its children, including Git's own proxy settings.
exec env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=http.proxy GIT_CONFIG_VALUE_0= \
  GIT_CONFIG_KEY_1=https.proxy GIT_CONFIG_VALUE_1= \
  uv sync --locked "$@"
