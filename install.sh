#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
version=$(<"$here/VERSION")
state="$STATE_DIR/state.json"

if [[ "${1:-}" == --preflight || "${1:-}" == --dry-run ]]; then
  bash "$here/preflight.sh"
  exit $?
fi
bash "$here/preflight.sh" || { echo "PREFLIGHT_BLOCKED: no installation changes made." >&2; exit 1; }
exec 9>/run/lock/vps-dr.lock
flock -n 9 || { echo "INSTALL_IN_PROGRESS: another bootstrap holds the lock." >&2; exit 1; }

if [[ -f "$state" ]]; then
  python3 "$here/scripts/dr.py" state-check "$state" "$XRAY_VERSION" "$version" "${VMESS_UUID:-}"
  uid=$(python3 "$here/scripts/dr.py" state-uuid "$state")
else
  uid=${VMESS_UUID:-}
  if [[ -z "$uid" && -t 2 ]]; then
    read -r -p 'Existing VMess UUID (blank generates a NEW UUID): ' uid </dev/tty
  fi
  if [[ -z "$uid" ]]; then
    uid=$(python3 "$here/scripts/dr.py" new-uuid)
    printf '%s\n' '*** NEW UUID GENERATED ***' '*** EXISTING CLIENT CONFIG WILL NOT MATCH ***' '*** CLIENTS MUST BE UPDATED ***' >&2
  fi
  python3 "$here/scripts/dr.py" state-init "$state" "$uid" "$XRAY_VERSION" "$version"
  chmod 700 "$STATE_DIR"
fi

if [[ -e /opt/vps-dr ]]; then
  [[ -d /opt/vps-dr && ! -L /opt/vps-dr ]] || { echo "CONFIG_CONFLICT: /opt/vps-dr is not a project directory." >&2; exit 1; }
  for file in VERSION install.sh preflight.sh health-check.sh export-client.sh uninstall.sh scripts/dr.py config/defaults.env config/xray.service; do
    cmp -s "$here/$file" "/opt/vps-dr/$file" || { echo "CONFIG_CONFLICT: deployed $file differs or is missing." >&2; exit 1; }
  done
fi

case "$(uname -m)" in
  x86_64) asset=Xray-linux-64.zip; expected=$XRAY_SHA256_AMD64; binary_expected=$XRAY_BINARY_SHA256_AMD64 ;;
  aarch64) asset=Xray-linux-arm64-v8a.zip; expected=$XRAY_SHA256_ARM64; binary_expected=$XRAY_BINARY_SHA256_ARM64 ;;
  *) echo "UNSUPPORTED_ARCH" >&2; exit 1 ;;
esac
tmp=$(mktemp -d)
stage=
trap 'rm -rf -- "$tmp"; [[ -z "$stage" ]] || rm -rf -- "$stage"' EXIT
base="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${asset}"
curl -fsSL --retry 3 "$base" -o "$tmp/$asset"
curl -fsSL --retry 3 "$base.dgst" -o "$tmp/$asset.dgst"
actual=$(sha256sum "$tmp/$asset" | awk '{print $1}')
published=$(awk '$1 == "SHA2-256=" {print $2}' "$tmp/$asset.dgst")
[[ "$actual" == "$expected" && "$published" == "$expected" ]] || {
  echo "CHECKSUM_MISMATCH: official archive, .dgst, and pinned SHA-256 must agree." >&2; exit 1;
}
python3 - "$tmp/$asset" "$tmp/xray" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    with archive.open('xray') as source, open(sys.argv[2], 'xb') as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
PY
chmod 755 "$tmp/xray"
[[ "$(sha256sum "$tmp/xray" | awk '{print $1}')" == "$binary_expected" ]] || { echo "CHECKSUM_MISMATCH: extracted Xray binary differs." >&2; exit 1; }
python3 "$here/scripts/dr.py" render "$tmp/config.json" "$uid"
"$tmp/xray" run -test -config "$tmp/config.json" >/dev/null || { echo "XRAY_CONFIG_TEST_FAIL" >&2; exit 1; }

if [[ -L /usr/local/bin/xray || -L /usr/local/etc/xray/config.json || -L /etc/systemd/system/xray.service ]]; then
  echo "EXISTING_XRAY_CONFLICT: symlink at managed path." >&2; exit 1
fi
if [[ -e /usr/local/bin/xray ]] && ! cmp -s "$tmp/xray" /usr/local/bin/xray; then
  echo "EXISTING_XRAY_CONFLICT: binary differs from pinned official asset." >&2; exit 1
fi
if [[ -e /usr/local/etc/xray/config.json ]]; then
  [[ "$(stat -c '%a %u' /usr/local/etc/xray/config.json)" == '600 0' ]] || { echo "CONFIG_CONFLICT: config permissions are unsafe." >&2; exit 1; }
  python3 "$here/scripts/dr.py" config-check /usr/local/etc/xray/config.json "$uid"
fi
if [[ -e /etc/systemd/system/xray.service ]] && ! cmp -s "$here/config/xray.service" /etc/systemd/system/xray.service; then
  echo "EXISTING_XRAY_CONFLICT: systemd unit differs." >&2; exit 1
fi

if [[ ! -e /usr/local/bin/xray ]]; then install -m 755 "$tmp/xray" /usr/local/bin/xray; fi
if [[ ! -e /usr/local/etc/xray/config.json ]]; then
  install -d -m 700 /usr/local/etc/xray
  install -m 600 "$tmp/config.json" /usr/local/etc/xray/config.json
fi
if [[ ! -e /etc/systemd/system/xray.service ]]; then
  install -m 644 "$here/config/xray.service" /etc/systemd/system/xray.service
  systemctl daemon-reload
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  if ! ufw status | grep -Eq '^443/tcp[[:space:]]+ALLOW'; then ufw allow 443/tcp; fi
fi
if systemctl is-active --quiet xray.service; then
  systemctl is-enabled --quiet xray.service || systemctl enable xray.service
  outcome=ALREADY_CONFIGURED
else
  systemctl enable --now xray.service
  outcome=INSTALL
fi
python3 "$here/scripts/dr.py" health "$state"

if [[ ! -e /opt/vps-dr ]]; then
  stage=$(mktemp -d /opt/.vps-dr.XXXXXX)
  install -d -m 755 "$stage/scripts" "$stage/config"
  install -m 755 "$here/"*.sh "$stage/"
  install -m 755 "$here/scripts/dr.py" "$stage/scripts/"
  install -m 644 "$here/VERSION" "$stage/"
  install -m 644 "$here/config/defaults.env" "$here/config/xray.service" "$stage/config/"
  chmod 755 "$stage"
  mv -T "$stage" /opt/vps-dr
  stage=
fi
echo "$outcome"
echo "Provider firewall/security group must allow TCP 443. SSH configuration was not changed."
echo "Production DNS was not changed."

if [[ -t 1 ]]; then
  server=$(python3 "$here/scripts/dr.py" public-ip || true)
  if [[ -n "$server" ]]; then
    if ! command -v qrencode >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
      if ! (apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qrencode >/dev/null); then
        echo "QR_TOOL_INSTALL_FAILED: Xray is healthy; client URI will still be shown." >&2
      fi
    fi
    if command -v qrencode >/dev/null 2>&1; then
      bash "$here/export-client.sh" --server "$server" --qr
    else
      bash "$here/export-client.sh" --server "$server"
      echo "QR_CODE_DISPLAY: UNAVAILABLE"
    fi
  else
    echo "CLIENT_EXPORT_UNAVAILABLE: Xray is healthy; public IP could not be detected." >&2
    echo "QR_CODE_DISPLAY: UNAVAILABLE"
  fi
else
  echo "CLIENT_EXPORT_SKIPPED: terminal output is not interactive."
fi
