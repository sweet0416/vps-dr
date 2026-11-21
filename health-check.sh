#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "ROOT_REQUIRED: 请使用 root 用户。" >&2; exit 1; }
python3 "$here/scripts/dr.py" health /etc/vps-dr/state.json
