#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
[[ "$(stat -c '%a %u' /etc/x-ui/install-result.env)" == '600 0' ]] || { echo "Credential file permissions are unsafe." >&2; exit 1; }
source /etc/x-ui/install-result.env
export XUI_PANEL_PORT XUI_WEB_BASE_PATH XUI_API_TOKEN
python3 "$here/scripts/dr.py" export /etc/vps-dr/state.json
