#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
if (( $# == 0 )); then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json
elif (( $# == 2 )) && [[ "$1" == --server ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json "$2"
else
  echo "Usage: export-client.sh [--server NEW_VPS_IP]" >&2
  exit 2
fi
