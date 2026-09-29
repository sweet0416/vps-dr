#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
python3 "$here/scripts/dr.py" state-check "$STATE_DIR/state.json" "$XRAY_VERSION" "$(<"$here/VERSION")"
[[ -f /etc/systemd/system/xray.service && ! -L /etc/systemd/system/xray.service ]] && cmp -s "$here/config/xray.service" /etc/systemd/system/xray.service || {
  echo "EXISTING_XRAY_CONFLICT: unit is missing or not owned by this project." >&2; exit 1;
}
if [[ "${1:-}" != --yes ]]; then
  read -r -p "Disable this project's Xray service? Type REMOVE: " answer </dev/tty
  [[ "$answer" == REMOVE ]] || { echo "Cancelled."; exit 1; }
fi
systemctl disable --now xray.service
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload
echo "Xray service disabled and project unit removed. Binary, root-only config/state, and firewall rules were retained for recovery."
