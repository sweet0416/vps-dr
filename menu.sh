#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo 'ROOT_REQUIRED: 请使用 root 用户运行此菜单。' >&2; exit 1; }
[[ -r /dev/tty ]] && { exec 3</dev/tty; } 2>/dev/null || {
  echo 'TTY_REQUIRED: 中文菜单需要 SSH 交互终端；自动安装请使用 bootstrap.sh --install。' >&2
  exit 1
}
bold='' accent='' reset=''
if [[ -t 1 && -n ${TERM:-} && $TERM != dumb && -z ${NO_COLOR:-} ]]; then
  bold=$'\033[1m'
  accent=$'\033[1;96m'
  reset=$'\033[0m'
fi

menu_item() {
  printf '  %s[%s]%s  %s%s%s\n\n' "$accent" "$1" "$reset" "$bold" "$2" "$reset"
}

show_menu() {
  printf '\n  %sVPS 中文节点管理%s\n\n' "$bold" "$reset"
  menu_item 1 '一键安装 / 继续安装'
  menu_item 2 '显示节点链接和二维码'
  menu_item 3 '检查运行状态'
  menu_item 4 '重启节点服务'
  menu_item 5 '仅升级管理脚本（保留节点）'
  menu_item 0 '退出'
  printf '  %s请选择 [0-5]：%s' "$bold" "$reset"
}

show_client() {
  local server=${VPS_DR_SERVER:-${VPS_DR_DOMAIN:-}}
  if [[ -z "$server" ]]; then
    server=$(python3 "$here/scripts/dr.py" public-ip 2>/dev/null) || server=
  fi
  if [[ -z "$server" ]]; then
    printf '自动检测失败，请输入这台 VPS 的公网 IPv4（或显式使用的域名）：'
    read -r server <&3 || return 1
  fi
  server=$(python3 "$here/scripts/dr.py" validate-server "$server") || return $?
  if ! command -v qrencode >/dev/null 2>&1; then
    echo '正在尝试安装二维码工具……'
    if ! (command -v apt-get >/dev/null && apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qrencode); then
      echo 'QR_TOOL_INSTALL_FAILED: 二维码工具安装失败，仍将显示可复制的节点链接。'
    fi
  fi
  bash "$here/export-client.sh" --server "$server" --qr
}
restart_node() (
  exec 9>/run/lock/vps-dr.lock
  flock -n 9 || { echo 'INSTALL_IN_PROGRESS: 正在安装、升级或重启，请稍后重试。' >&2; exit 1; }
  [[ -f /etc/vps-dr/state.json && -f /etc/systemd/system/xray.service ]] || {
    echo 'STATE_REQUIRED: 未发现已安装节点，请先选择 1。' >&2; exit 1;
  }
  bash "$here/preflight.sh" --local || exit $?
  systemctl restart xray.service || { echo 'RESTART_FAILED: 服务重启失败，请查看 systemd 日志。' >&2; exit 1; }
  bash "$here/health-check.sh"
)
last_status=0
while true; do
  show_menu
  read -r choice <&3 || exit "$last_status"
  case "$choice" in
    1)
      echo '首次安装自动生成新 UUID；继续安装复用当前机器 UUID。'
      if bash "$here/install.sh"; then last_status=0; else last_status=$?; fi
      ;;
    2)
      if [[ -f /etc/vps-dr/state.json ]]; then
        if show_client; then last_status=0; else last_status=$?; fi
      else echo 'STATE_REQUIRED: 尚未发现节点状态，请先选择 1。'; last_status=1; fi
      ;;
    3)
      if bash "$here/health-check.sh"; then last_status=0; else last_status=$?; fi
      ;;
    4)
      if restart_node; then last_status=0; else last_status=$?; fi
      ;;
    5)
      if bash "$here/install.sh" --upgrade-management; then last_status=0; else last_status=$?; fi
      ;;
    0) exit "$last_status" ;;
    *) echo '请输入 0、1、2、3、4 或 5。'; last_status=2; continue ;;
  esac
  if (( last_status == 0 )); then
    echo '操作完成。客户端实际连通性请在客户端测试。'
  else
    printf '操作未完成（退出码 %s），请根据上方提示处理后重试。\n' "$last_status" >&2
  fi
done
