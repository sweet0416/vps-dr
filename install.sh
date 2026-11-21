#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
version=$(<"$here/VERSION")
state="$STATE_DIR/state.json"

if (( $# > 1 )) || [[ "${1:---install}" != --install && "${1:-}" != --preflight && "${1:-}" != --dry-run && "${1:-}" != --upgrade-management ]]; then
  echo '用法：install.sh [--install|--preflight|--dry-run|--upgrade-management]' >&2
  exit 2
fi
if [[ "${1:-}" == --preflight || "${1:-}" == --dry-run ]]; then
  bash "$here/preflight.sh"
  exit $?
fi
(( EUID == 0 )) || { echo 'ROOT_REQUIRED: 请使用 root 用户。' >&2; exit 1; }
if [[ ${VPS_DR_LOCKED:-} != 1 ]] || ! python3 "$here/scripts/locked.py" check; then
  exec python3 "$here/scripts/locked.py" run bash "$here/install.sh" "$@"
fi
python3 "$here/scripts/update.py" recover --lock-held
python3 "$here/scripts/backup.py" recover --lock-held
python3 "$here/scripts/manage.py" recover /opt/vps-dr
python3 "$here/scripts/manage.py" shortcut-check "$here" /usr/local/bin/caidan
if [[ "${1:-}" == --upgrade-management ]]; then
  [[ -f "$state" ]] || { echo 'STATE_REQUIRED: 尚未安装，请选择菜单 1。' >&2; exit 1; }
  bash "$here/preflight.sh" --local || exit 1
  python3 "$here/scripts/manage.py" upgrade "$here" /opt/vps-dr
  python3 "$here/scripts/manage.py" shortcut-install "$here" /usr/local/bin/caidan
  python3 /opt/vps-dr/scripts/traffic.py setup || echo 'TRAFFIC_SETUP_FAILED: 节点管理升级已完成；流量采集未能启用，请按上方提示处理后再次选择 5。' >&2
  exit 0
fi
bash "$here/preflight.sh" || { echo 'PREFLIGHT_BLOCKED: 预检未通过，未执行安装。' >&2; exit 1; }
python3 "$here/scripts/manage.py" check "$here" /opt/vps-dr

if [[ -f "$state" ]]; then
  python3 "$here/scripts/dr.py" state-check "$state" "$XRAY_VERSION" "$version" "${VMESS_UUID:-}"
  uid=$(python3 "$here/scripts/dr.py" state-uuid "$state")
else
  uid=${VMESS_UUID:-}
  if [[ -z "$uid" ]]; then
    uid=$(python3 "$here/scripts/dr.py" new-uuid)
    echo '已为新节点生成 UUID；完成后请导入本次节点配置。' >&2
  fi
  python3 "$here/scripts/dr.py" state-init "$state" "$uid" "$XRAY_VERSION" "$version"
  chmod 700 "$STATE_DIR"
fi


case "$(uname -m)" in
  x86_64) asset=Xray-linux-64.zip; expected=$XRAY_SHA256_AMD64; binary_expected=$XRAY_BINARY_SHA256_AMD64 ;;
  aarch64) asset=Xray-linux-arm64-v8a.zip; expected=$XRAY_SHA256_ARM64; binary_expected=$XRAY_BINARY_SHA256_ARM64 ;;
  *) echo "UNSUPPORTED_ARCH" >&2; exit 1 ;;
esac
tmp=$(mktemp -d)
stage=
trap 'rm -rf -- "$tmp"; [[ -z "$stage" ]] || rm -rf -- "$stage"' EXIT
if [[ -e /usr/local/bin/xray ]]; then
  python3 "$here/scripts/update.py" installed-binary-check "$state"
  cp /usr/local/bin/xray "$tmp/xray"
else
base="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${asset}"
curl -fsSL --connect-timeout 15 --max-time 300 --retry 2 "$base" -o "$tmp/$asset"
curl -fsSL --connect-timeout 15 --max-time 60 --retry 2 "$base.dgst" -o "$tmp/$asset.dgst"
actual=$(sha256sum "$tmp/$asset" | awk '{print $1}')
published=$(awk '$1 == "SHA2-256=" {print $2}' "$tmp/$asset.dgst")
[[ "$actual" == "$expected" && "$published" == "$expected" ]] || {
  echo "CHECKSUM_MISMATCH: 官方压缩包、摘要与固定 SHA-256 不一致，已停止。" >&2; exit 1;
}
python3 - "$tmp/$asset" "$tmp/xray" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    with archive.open('xray') as source, open(sys.argv[2], 'xb') as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
PY
chmod 755 "$tmp/xray"
[[ "$(sha256sum "$tmp/xray" | awk '{print $1}')" == "$binary_expected" ]] || { echo "CHECKSUM_MISMATCH: 解压后的 Xray 二进制校验失败，已停止。" >&2; exit 1; }
fi
python3 "$here/scripts/dr.py" render "$tmp/config.json" "$uid"
"$tmp/xray" run -test -config "$tmp/config.json" >/dev/null || { echo "XRAY_CONFIG_TEST_FAIL" >&2; exit 1; }

if [[ -L /usr/local/bin/xray || -L /usr/local/etc/xray/config.json || -L /etc/systemd/system/xray.service ]]; then
  echo "EXISTING_XRAY_CONFLICT: 受管路径包含符号链接，已停止。" >&2; exit 1
fi
if [[ -e /usr/local/bin/xray ]] && ! cmp -s "$tmp/xray" /usr/local/bin/xray; then
  echo "EXISTING_XRAY_CONFLICT: 现有二进制在检查后发生变化，已保留。" >&2; exit 1
fi
if [[ -e /usr/local/etc/xray/config.json ]]; then
  [[ "$(stat -c '%a %u' /usr/local/etc/xray/config.json)" == '600 0' ]] || { echo "CONFIG_CONFLICT: 配置权限不安全，应为 root 所有且权限 600。" >&2; exit 1; }
  python3 "$here/scripts/dr.py" config-check /usr/local/etc/xray/config.json "$uid"
fi
if [[ -e /etc/systemd/system/xray.service ]] && ! cmp -s "$here/config/xray.service" /etc/systemd/system/xray.service; then
  echo "EXISTING_XRAY_CONFLICT: 现有 systemd 服务文件与本项目不符，已保留。" >&2; exit 1
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
python3 "$here/scripts/manage.py" upgrade "$here" /opt/vps-dr
python3 "$here/scripts/manage.py" shortcut-install "$here" /usr/local/bin/caidan
python3 /opt/vps-dr/scripts/traffic.py setup || echo 'TRAFFIC_SETUP_FAILED: 流量采集暂不可用，节点安装将继续；处理上方提示后再次选择 5 可重试。' >&2

if systemctl is-active --quiet xray.service; then
  systemctl is-enabled --quiet xray.service || systemctl enable xray.service
  outcome=ALREADY_CONFIGURED
else
  systemctl enable --now xray.service
  outcome=INSTALL
fi

python3 "$here/scripts/dr.py" health "$state"
echo "$outcome"
echo "请在服务商防火墙／安全组放行 TCP 443，然后从客户端测试连接。"
echo "域名为可选项；本次未修改 DNS。"

if [[ -t 1 ]]; then
  server=${VPS_DR_SERVER:-$(python3 "$here/scripts/dr.py" public-ip || true)}
  if [[ -n "$server" ]]; then
    if ! command -v qrencode >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
      if ! (apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qrencode >/dev/null); then
        echo "QR_TOOL_INSTALL_FAILED: 二维码工具安装失败，本机服务正常，仍会显示节点链接。" >&2
      fi
    fi
    if command -v qrencode >/dev/null 2>&1; then
      bash "$here/export-client.sh" --server "$server" --qr || echo "CLIENT_EXPORT_FAILED: 节点服务正常，导出失败，请选择菜单 2 重试。" >&2
    else
      bash "$here/export-client.sh" --server "$server" || echo "CLIENT_EXPORT_FAILED: 节点服务正常，导出失败，请选择菜单 2 重试。" >&2
      echo "QR_CODE_DISPLAY: UNAVAILABLE"
    fi
  else
    echo "CLIENT_EXPORT_UNAVAILABLE: 本机服务正常，但公网 IP 获取失败；请返回菜单 2 手动输入。" >&2
    echo "QR_CODE_DISPLAY: UNAVAILABLE"
  fi
else
  echo "CLIENT_EXPORT_SKIPPED: 当前输出不是交互终端，未显示客户端密钥；请在终端选择菜单 2。"
fi

printf "\n安装流程完成。中文管理菜单：bash /opt/vps-dr/menu.sh\n"
