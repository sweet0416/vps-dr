#!/usr/bin/env bash
set -Eeuo pipefail
(( EUID == 0 )) || { echo "Run with sudo." >&2; exit 1; }
[[ -f /etc/vps-dr/state.json ]] || { echo "No vps-dr state found; refusing to remove a non-project installation." >&2; exit 1; }
[[ -x /usr/local/x-ui/x-ui && -f /etc/x-ui/install-result.env ]] || { echo "Partial installation: inspect manually; no files removed." >&2; exit 1; }
if [[ "${1:-}" != --yes ]]; then
  read -r -p "Disable this project's 3x-ui service? Type REMOVE: " answer </dev/tty
  [[ "$answer" == REMOVE ]] || { echo "Cancelled."; exit 1; }
fi
systemctl disable --now x-ui
rm -f /etc/systemd/system/x-ui.service /usr/bin/x-ui
systemctl daemon-reload
# Preserve the database, credentials, binary, and UFW rules for manual recovery.
echo "3x-ui service disabled and service unit removed. /usr/local/x-ui, /etc/x-ui, /etc/vps-dr and UFW rules were retained for recovery."
