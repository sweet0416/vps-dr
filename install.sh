#!/usr/bin/env bash
set -Eeuo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
trap 'echo "ERROR: install failed at line $LINENO. DNS was not changed. Review root-only /var/log/vps-dr-install.log if upstream installation failed." >&2' ERR

if [[ "${1:-}" == --preflight || "${1:-}" == --dry-run || "${DRY_RUN:-0}" == 1 ]]; then
  if bash "$here/preflight.sh"; then exit 0; else exit 1; fi
fi
bash "$here/preflight.sh" || { echo "PREFLIGHT_BLOCKED: no system changes made." >&2; exit 1; }
[[ "$VERSION_XRAY" == "$XRAY_BUNDLED" && "$XRAY_STABLE_COMPATIBLE" == YES ]] || {
  echo "PRERELEASE_DEPENDENCY_BLOCKED: 3x-ui $VERSION_3XUI bundles $XRAY_BUNDLED; stable target $VERSION_XRAY is not supported by its core manager." >&2
  exit 1
}

(( EUID == 0 )) || { echo "Run as root (sudo bash install.sh)." >&2; exit 1; }

if [[ -e /opt/vps-dr ]]; then
  [[ -d /opt/vps-dr && -f /opt/vps-dr/VERSION && -f /opt/vps-dr/install.sh && -f /opt/vps-dr/scripts/dr.py ]] || {
    echo "CONFIG_CONFLICT: incomplete /opt/vps-dr; inspect before retry." >&2; exit 1;
  }
  for file in VERSION install.sh scripts/dr.py config/defaults.env; do
    cmp -s "$here/$file" "/opt/vps-dr/$file" || { echo "CONFIG_CONFLICT: deployed $file differs; no overwrite." >&2; exit 1; }
  done
fi
if [[ ! -f "$STATE_DIR/state.json" ]] && { [[ -e /usr/local/x-ui ]] || [[ -e /etc/x-ui ]] || [[ -e /etc/systemd/system/x-ui.service ]] || [[ -e /opt/vps-dr ]]; }; then
  echo "CONFIG_CONFLICT: existing 3x-ui files are not owned by this project." >&2; exit 1
fi

if [[ -e "$STATE_DIR/state.json" ]]; then
  python3 "$here/scripts/dr.py" check-state "$STATE_DIR/state.json" "$VERSION_3XUI" "$VERSION_XRAY" "$(<"$here/VERSION")" "${VMESS_UUID:-}"
  VMESS_UUID=$(python3 "$here/scripts/dr.py" state-uuid "$STATE_DIR/state.json")
else
  if [[ -z "${VMESS_UUID:-}" && -t 2 ]]; then
    read -r -p 'Existing VMess UUID (required): ' VMESS_UUID </dev/tty
  fi
  [[ -n "${VMESS_UUID:-}" ]] || { echo "VMESS_UUID required; it will never be regenerated automatically." >&2; exit 1; }
fi
if [[ ! -x /usr/local/x-ui/x-ui ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl python3 iproute2 ufw tar openssl
fi
if [[ ! -e "$STATE_DIR/state.json" ]]; then
  python3 "$here/scripts/dr.py" init-state "$STATE_DIR/state.json" "$VERSION_3XUI" "$VERSION_XRAY" "$(<"$here/VERSION")" "$VMESS_UUID"
fi
export VMESS_UUID

fresh_install=NO
if [[ ! -x /usr/local/x-ui/x-ui ]]; then
  fresh_install=YES
  panel_port=$(shuf -i 20000-59999 -n 1)
  [[ -z "$(ss -ltnH "( sport = :$panel_port )")" ]] || { echo "PANEL_PORT_CONFLICT: selected port is occupied; retry." >&2; exit 1; }
  # Protect the current SSH session before enabling the firewall.
  ufw allow 22/tcp
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    ssh_port=${SSH_CONNECTION##* }
    [[ "$ssh_port" =~ ^[0-9]+$ ]] && (( ssh_port >= 1 && ssh_port <= 65535 )) || { echo "Invalid SSH_CONNECTION port." >&2; exit 1; }
    ufw allow "$ssh_port/tcp"
  fi
  while IFS= read -r ssh_port; do
    [[ "$ssh_port" =~ ^[0-9]+$ ]] && (( ssh_port >= 1 && ssh_port <= 65535 )) || continue
    ufw allow "$ssh_port/tcp"
  done < <(ss -ltnpH | awk '/"sshd"/ {sub(/^.*:/, "", $4); print $4}' | sort -u)
  ufw allow 443/tcp
  ufw default deny incoming
  ufw --force enable
  log=/var/log/vps-dr-install.log
  install -m 600 /dev/null "$log"
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  curl -fsSL --retry 3 "https://raw.githubusercontent.com/MHSanaei/3x-ui/${VERSION_3XUI_COMMIT}/install.sh" -o "$installer"
  XUI_NONINTERACTIVE=1 XUI_SSL_MODE=none XUI_PANEL_PORT="$panel_port" XUI_ENABLE_FAIL2BAN=false bash "$installer" "$VERSION_3XUI" >"$log" 2>&1 || {
    echo "Official 3x-ui installer failed; root-only log: $log" >&2; exit 1;
  }
  rm -f "$installer"
  trap - EXIT
fi

result=/etc/x-ui/install-result.env
[[ -r "$result" ]] || { echo "CONFIG_CONFLICT: missing upstream install-result.env; inspect install log." >&2; exit 1; }
[[ "$(stat -c '%a %u' "$result")" == '600 0' ]] || { echo "CONFIG_CONFLICT: upstream credential file must be root-owned mode 600." >&2; exit 1; }
# Upstream writes this file mode 600 and shell-quotes its generated values.
source "$result"
[[ "${XUI_PANEL_PORT:-}" =~ ^[0-9]+$ && "${XUI_WEB_BASE_PATH:-}" =~ ^[a-zA-Z0-9]+$ && -n "${XUI_API_TOKEN:-}" ]] || {
  echo "Invalid upstream installation result." >&2; exit 1;
}
if ! /usr/local/x-ui/x-ui setting -getListen true | grep -q 'listenIP: 127.0.0.1'; then
  [[ "$fresh_install" == YES ]] || { echo "CONFIG_CONFLICT: existing panel is not localhost-only; refusing to overwrite." >&2; exit 1; }
  /usr/local/x-ui/x-ui setting -listenIP 127.0.0.1 >/dev/null
  systemctl restart x-ui
fi
systemctl is-active --quiet x-ui || { echo "3x-ui did not start." >&2; exit 1; }
export XUI_PANEL_PORT XUI_WEB_BASE_PATH XUI_API_TOKEN
python3 "$here/scripts/dr.py" configure "$STATE_DIR/state.json"
python3 "$here/scripts/dr.py" verify "$STATE_DIR/state.json"
python3 "$here/scripts/dr.py" export "$STATE_DIR/state.json"
if [[ ! -e /opt/vps-dr ]]; then
  install -d -m 755 /opt/vps-dr /opt/vps-dr/config /opt/vps-dr/scripts /opt/vps-dr/docs
  install -m 755 "$here/"*.sh /opt/vps-dr/
  install -m 755 "$here/scripts/dr.py" /opt/vps-dr/scripts/
  install -m 644 "$here/config/defaults.env" /opt/vps-dr/config/
  install -m 644 "$here/VERSION" /opt/vps-dr/
  install -m 755 "$here/preflight.sh" /opt/vps-dr/
  install -m 644 "$here/README.md" /opt/vps-dr/
  install -m 644 "$here/docs/"*.md /opt/vps-dr/docs/
fi
echo "Panel: ssh -L ${XUI_PANEL_PORT}:127.0.0.1:${XUI_PANEL_PORT} root@NEW_VPS_IP"
echo "Then open http://127.0.0.1:${XUI_PANEL_PORT}/${XUI_WEB_BASE_PATH}/"
echo "Panel credentials (root-only): $result — save them to your password manager immediately."
echo "Cloudflare DNS is unchanged. Switch A/node to the new public IP manually after client testing."
