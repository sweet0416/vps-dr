#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"

os=UNKNOWN arch=$(uname -m) root=NO network=NO dns=NO domain_dns=NO
disk=UNKNOWN memory=UNKNOWN systemd=NO port443=FREE owner=NONE
existing=NO config=NO firewall=NOT_INSTALLED ready=YES
reasons=()
block() { ready=NO; reasons+=("$1"); }

if [[ -r /etc/os-release ]]; then
  source /etc/os-release
  os="${ID:-UNKNOWN}:${VERSION_ID:-UNKNOWN}"
fi
case "$os" in ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;; *) block UNSUPPORTED_OS;; esac
case "$arch" in x86_64|aarch64) ;; *) block UNSUPPORTED_ARCH;; esac
if (( EUID == 0 )); then root=YES; else block ROOT_REQUIRED; fi
for cmd in curl python3 sha256sum ss systemctl getent df awk grep stat readlink cmp wc flock; do
  command -v "$cmd" >/dev/null 2>&1 || block "MISSING_${cmd^^}"
done
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then systemd=YES; else block SYSTEMD_REQUIRED; fi
[[ -d /run/lock ]] || block RUN_LOCK_UNAVAILABLE
if command -v getent >/dev/null 2>&1 && getent ahostsv4 github.com >/dev/null 2>&1; then dns=YES; else block DNS_UNAVAILABLE; fi
if command -v getent >/dev/null 2>&1 && getent ahostsv4 "$DOMAIN" >/dev/null 2>&1; then domain_dns=YES; else block DOMAIN_DNS_UNAVAILABLE; fi
if [[ "$arch" == aarch64 ]]; then asset=Xray-linux-arm64-v8a.zip; else asset=Xray-linux-64.zip; fi
if command -v curl >/dev/null 2>&1 && curl -fsSIL --max-time 12 "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${asset}.dgst" >/dev/null 2>&1; then network=YES; else block NETWORK_UNAVAILABLE; fi
if command -v df >/dev/null 2>&1; then disk=$(df -Pm / 2>/dev/null | awk 'NR==2 {print $4}' || true); fi
[[ "$disk" =~ ^[0-9]+$ ]] && (( disk >= 1024 )) || block DISK_BELOW_1_GIB
if [[ -r /proc/meminfo ]]; then memory=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo); fi
[[ "$memory" =~ ^[0-9]+$ ]] && (( memory >= 256 )) || block MEMORY_BELOW_256_MIB

if command -v ufw >/dev/null 2>&1; then
  firewall=$(ufw status 2>/dev/null | awk 'NR==1 {print $2}' || true)
  firewall="UFW_${firewall:-UNKNOWN}"
fi
[[ -e /usr/local/bin/xray || -L /usr/local/bin/xray ]] && existing=YES
[[ -e /usr/local/etc/xray/config.json || -L /usr/local/etc/xray/config.json ]] && config=YES
state=NO
[[ -f "$STATE_DIR/state.json" ]] && state=YES
if [[ -L /usr/local/bin/xray || -L /usr/local/etc/xray || -L /usr/local/etc/xray/config.json || -L /etc/systemd/system/xray.service || -L /opt/vps-dr || -L "$STATE_DIR" || -L "$STATE_DIR/state.json" ]]; then block EXISTING_XRAY_CONFLICT; fi
if [[ -e /etc/systemd/system/xray.service.d || -e /run/systemd/system/xray.service.d ]]; then block EXISTING_XRAY_CONFLICT; fi
if [[ "$state" == NO ]]; then
  other_xray=$(command -v xray 2>/dev/null || true)
  if [[ "$existing" == YES || "$config" == YES || -e /etc/systemd/system/xray.service || -e /opt/vps-dr || -n "$other_xray" ]] || { command -v systemctl >/dev/null 2>&1 && systemctl cat xray.service >/dev/null 2>&1; }; then
    block EXISTING_XRAY_CONFLICT
  fi
else
  if command -v python3 >/dev/null 2>&1 && ! python3 "$here/scripts/dr.py" state-check "$STATE_DIR/state.json" "$XRAY_VERSION" "$(<"$here/VERSION")" >/dev/null 2>&1; then block CONFIG_CONFLICT; fi
  if [[ "$existing" == YES ]]; then
    if [[ ! -x /usr/local/bin/xray ]] || ! version_output=$(/usr/local/bin/xray version 2>/dev/null) || [[ "$version_output" != "Xray ${XRAY_VERSION#v} "* ]]; then block EXISTING_XRAY_CONFLICT; fi
    if [[ "$arch" == x86_64 ]]; then binary_sha=$XRAY_BINARY_SHA256_AMD64; else binary_sha=$XRAY_BINARY_SHA256_ARM64; fi
    if [[ "$(sha256sum /usr/local/bin/xray 2>/dev/null | awk '{print $1}')" != "$binary_sha" ]]; then block EXISTING_XRAY_CONFLICT; fi
  fi
  if [[ "$config" == YES ]] && command -v python3 >/dev/null 2>&1 && ! python3 "$here/scripts/dr.py" config-check /usr/local/etc/xray/config.json "$(python3 "$here/scripts/dr.py" state-uuid "$STATE_DIR/state.json" 2>/dev/null || true)" >/dev/null 2>&1; then block CONFIG_CONFLICT; fi
  if [[ "$config" == YES && "$(stat -c '%a %u' /usr/local/etc/xray/config.json 2>/dev/null)" != '600 0' ]]; then block CONFIG_PERMISSIONS_UNSAFE; fi
  if [[ -e /etc/systemd/system/xray.service ]] && ! cmp -s "$here/config/xray.service" /etc/systemd/system/xray.service; then block EXISTING_XRAY_CONFLICT; fi
  if [[ ! -e /etc/systemd/system/xray.service ]] && command -v systemctl >/dev/null 2>&1 && systemctl cat xray.service >/dev/null 2>&1; then block EXISTING_XRAY_CONFLICT; fi
fi

if command -v ss >/dev/null 2>&1; then
  listeners=$(ss -ltnpH '( sport = :443 )' 2>/dev/null || true)
  if [[ -n "$listeners" ]]; then
    port443=LISTENING
    if (( $(printf '%s\n' "$listeners" | wc -l) != 1 )); then block PORT_443_CONFLICT; fi
    owner=$(printf '%s\n' "$listeners" | awk -F '"' 'NR==1 && NF>=2 {print $2}')
    owner=${owner:-UNKNOWN}
    pid=0
    [[ "$listeners" =~ pid=([0-9]+) ]] && pid=${BASH_REMATCH[1]}
    service_pid=$(systemctl show --property=MainPID --value xray.service 2>/dev/null || true)
    exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
    if [[ "$state" == YES && "$pid" != 0 && "$service_pid" == "$pid" && "$exe" == /usr/local/bin/xray ]]; then
      port443=MANAGED_XRAY
      if [[ "$config" != YES || ! -f /etc/systemd/system/xray.service ]]; then block PARTIAL_ACTIVE_XRAY; fi
    else
      block PORT_443_CONFLICT
    fi
    owner="$owner pid=$pid"
  fi
fi

printf '=== VPS_DR_PREFLIGHT ===\nOS: %s\nARCH: %s\nROOT: %s\nSYSTEMD: %s\nNETWORK: %s\nDNS: %s\nDOMAIN_DNS: %s\nPORT_443: %s\nPORT_443_OWNER: %s\nEXISTING_XRAY: %s\nEXISTING_CONFIG: %s\nFIREWALL: %s\nDISK: %s MiB available\nMEMORY: %s MiB available\nREADY_TO_INSTALL: %s\nBLOCKERS: %s\n' \
  "$os" "$arch" "$root" "$systemd" "$network" "$dns" "$domain_dns" "$port443" "$owner" "$existing" "$config" "$firewall" "$disk" "$memory" "$ready" "${reasons[*]:-NONE}"
[[ "$ready" == YES ]]
