#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
python3 "$here/scripts/dr.py" health /etc/vps-dr/state.json
