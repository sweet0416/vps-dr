#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "请使用 root 用户运行。" >&2; exit 1; }
python3 "$here/scripts/dr.py" config-check-state /usr/local/etc/xray/config.json /etc/vps-dr/state.json
if (( $# == 0 )); then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json
elif (( $# == 1 )) && [[ "$1" == --qr ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json --qr
elif (( $# == 2 )) && [[ "$1" == --server ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json "$2"
elif (( $# == 3 )) && [[ "$1" == --server && "$3" == --qr ]]; then
  exec python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json "$2" --qr
else
  echo "用法： export-client.sh [--server IPv4或域名] [--qr]" >&2
  exit 2
fi
