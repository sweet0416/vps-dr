#!/usr/bin/env bash
set -Eeuo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
os=UNKNOWN arch=$(uname -m) root=NO network=NO dns=NO port443=FREE
panel_port=UNASSIGNED disk=UNKNOWN memory=UNKNOWN supported=YES ready=YES
reasons=()
fail() { supported=NO; ready=NO; reasons+=("$1"); }
if [[ -r /etc/os-release ]]; then
  source /etc/os-release
  os="${ID:-UNKNOWN}:${VERSION_ID:-UNKNOWN}"
fi
case "$os" in ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;; *) fail UNSUPPORTED_OS;; esac
case "$arch" in x86_64|aarch64) ;; *) fail UNSUPPORTED_ARCH;; esac
if (( EUID == 0 )); then root=YES; else fail ROOT_REQUIRED; fi
for cmd in curl tar python3 ss systemctl apt-get getent df awk sed stat cmp; do
  command -v "$cmd" >/dev/null 2>&1 || fail "MISSING_${cmd^^}"
done
if command -v systemctl >/dev/null 2>&1 && ! [[ -d /run/systemd/system ]]; then fail SYSTEMD_REQUIRED; fi
if command -v getent >/dev/null 2>&1 && getent ahostsv4 github.com >/dev/null 2>&1 && getent ahostsv4 raw.githubusercontent.com >/dev/null 2>&1; then dns=YES; else fail DNS_UNAVAILABLE; fi
if command -v curl >/dev/null 2>&1 && curl -fsSI --max-time 8 https://github.com/ >/dev/null 2>&1 && curl -fsSI --max-time 8 "https://raw.githubusercontent.com/MHSanaei/3x-ui/${VERSION_3XUI_COMMIT}/install.sh" >/dev/null 2>&1; then network=YES; else fail NETWORK_UNAVAILABLE; fi
if command -v df >/dev/null 2>&1; then
  disk=$(df -Pm / | awk 'NR==2 {print $4}')
  [[ "$disk" =~ ^[0-9]+$ ]] && (( disk >= 1024 )) || fail DISK_BELOW_1_GIB
fi
if [[ -r /proc/meminfo ]]; then
  memory=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
  [[ "$memory" =~ ^[0-9]+$ ]] && (( memory >= 256 )) || fail MEMORY_BELOW_256_MIB
else fail MEMORY_UNAVAILABLE; fi

state_present=NO
[[ -f "$STATE_DIR/state.json" ]] && state_present=YES
if [[ "$state_present" == NO ]] && { [[ -e /usr/local/x-ui ]] || [[ -e /etc/x-ui ]] || [[ -e /etc/systemd/system/x-ui.service ]] || [[ -e /opt/vps-dr ]]; }; then
  fail UNOWNED_3X_UI
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  if [[ "$state_present" == NO ]]; then
    ready=NO; reasons+=(FIREWALL_ALREADY_ACTIVE_REVIEW)
  elif [[ ! -x /usr/local/x-ui/x-ui ]]; then
    ready=NO; reasons+=(PARTIAL_FIREWALL_REQUIRES_REVIEW)
  fi
fi
if command -v ss >/dev/null 2>&1; then
  listeners=$(ss -ltnpH '( sport = :443 )' 2>/dev/null || true)
  if [[ -n "$listeners" ]]; then
    port443=LISTENING_UNKNOWN_OWNER
    if [[ "$state_present" == YES && "$listeners" =~ pid=([0-9]+) ]]; then
      owner=$(readlink -f "/proc/${BASH_REMATCH[1]}/exe" 2>/dev/null || true)
      if [[ "$owner" == /usr/local/x-ui/bin/xray-linux-* ]]; then port443=MANAGED_XRAY; fi
    fi
    [[ "$port443" == MANAGED_XRAY ]] || { ready=NO; reasons+=(PORT_443_CONFLICT); }
  fi
fi
if [[ "$state_present" == YES && -x /usr/local/x-ui/x-ui && -e /etc/x-ui/install-result.env ]]; then
  result=/etc/x-ui/install-result.env
  if [[ -r "$result" && "$(stat -c '%a %u' "$result" 2>/dev/null)" == '600 0' ]]; then
    panel_port=$(sed -n 's/^XUI_PANEL_PORT=\([0-9][0-9]*\)$/\1/p' "$result")
    [[ -n "$panel_port" ]] || fail PANEL_PORT_UNKNOWN
  else fail PANEL_CREDENTIALS_UNAVAILABLE; fi
  if [[ "$panel_port" =~ ^[0-9]+$ ]] && command -v ss >/dev/null 2>&1; then
    panel_listener=$(ss -ltnpH "( sport = :$panel_port )" 2>/dev/null || true)
    if [[ -n "$panel_listener" && "$panel_listener" != *'"x-ui"'* ]]; then ready=NO; reasons+=(PANEL_PORT_CONFLICT); fi
  fi
elif [[ "$state_present" == YES ]] && { [[ -e /usr/local/x-ui ]] || [[ -e /etc/x-ui ]] || [[ -e /etc/systemd/system/x-ui.service ]]; }; then
  ready=NO; reasons+=(PARTIAL_INSTALL_REQUIRES_REVIEW)
fi
if [[ "$VERSION_XRAY" != "$XRAY_BUNDLED" || "$XRAY_STABLE_COMPATIBLE" != YES ]]; then
  ready=NO; reasons+=(XRAY_STABLE_COMPATIBILITY_BLOCKED)
fi
printf '=== VPS_DR_PREFLIGHT ===\nOS: %s\nARCH: %s\nROOT: %s\nNETWORK: %s\nDNS: %s\nPORT_443: %s\nPANEL_PORT: %s\nDISK: %s MiB available\nMEMORY: %s MiB available\nSUPPORTED: %s\nREADY_TO_INSTALL: %s\n' \
  "$os" "$arch" "$root" "$network" "$dns" "$port443" "$panel_port" "$disk" "$memory" "$supported" "$ready"
printf 'BLOCKERS: %s\n' "${reasons[*]:-NONE}"
[[ "$ready" == YES ]]
