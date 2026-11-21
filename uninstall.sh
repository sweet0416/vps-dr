#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/config/defaults.env"
(( EUID == 0 )) || { echo "ROOT_REQUIRED: 请使用 root 用户。" >&2; exit 1; }
if [[ ${VPS_DR_LOCKED:-} != 1 ]] || ! python3 "$here/scripts/locked.py" check; then
  exec python3 "$here/scripts/locked.py" run bash "$here/uninstall.sh" "$@"
fi
python3 "$here/scripts/update.py" recover --lock-held
python3 "$here/scripts/backup.py" recover --lock-held
bash "$here/preflight.sh" --local
python3 "$here/scripts/dr.py" state-check "$STATE_DIR/state.json" "$XRAY_VERSION" "$(<"$here/VERSION")"
[[ -f /etc/systemd/system/xray.service && ! -L /etc/systemd/system/xray.service ]] && cmp -s "$here/config/xray.service" /etc/systemd/system/xray.service || {
  echo "EXISTING_XRAY_CONFLICT: 服务文件缺失或不属于本项目，已保留。" >&2; exit 1;
}
if [[ "${1:-}" != --yes ]]; then
  read -r -p "停用本项目 Xray 服务？确认请输入 REMOVE：" answer </dev/tty
  [[ "$answer" == REMOVE ]] || { echo "已取消。"; exit 1; }
fi
for unit in vps-dr-traffic.service vps-dr-traffic.timer; do
  path="/etc/systemd/system/$unit"
  if [[ -L "$path" || -e "$path.d" ]] || { [[ -e "$path" ]] && ! cmp -s "$here/config/$unit" "$path"; }; then
    echo "TRAFFIC_UNIT_CONFLICT: 流量单元存在未知修改，已保留。" >&2; exit 1
  fi
done
if [[ -f /etc/systemd/system/vps-dr-traffic.timer ]]; then systemctl disable --now vps-dr-traffic.timer; fi
if [[ -f /etc/systemd/system/vps-dr-traffic.service ]]; then systemctl stop vps-dr-traffic.service; fi
rm -f /etc/systemd/system/vps-dr-traffic.service /etc/systemd/system/vps-dr-traffic.timer
systemctl disable --now xray.service
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload
echo "已停用 Xray 与流量采集并移除本项目服务文件。程序、私密配置、状态、备份、流量历史和防火墙规则已保留。BBR 设置可通过菜单 12 单独恢复。"
