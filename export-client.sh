#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
if (( $# == 0 )); then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json
elif (( $# == 1 )) && [[ "$1" == --qr ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json --qr
elif (( $# == 2 )) && [[ "$1" == --server ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json "$2"
elif (( $# == 3 )) && [[ "$1" == --server && "$3" == --qr ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json "$2" --qr
else
  echo "Usage: export-client.sh [--server NEW_VPS_IP] [--qr]" >&2
  exit 2
fi
