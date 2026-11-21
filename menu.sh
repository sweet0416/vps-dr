#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
(( EUID == 0 )) || { echo 'ROOT_REQUIRED: 请使用 root 用户运行此菜单。' >&2; exit 1; }
[[ -r /dev/tty ]] && { exec 3</dev/tty; } 2>/dev/null || {
  echo 'TTY_REQUIRED: 中文菜单需要 SSH 交互终端；自动安装请使用 bootstrap.sh --install。' >&2
  exit 1
}
bold='' accent='' good='' reset=''
if [[ -t 1 && -n ${TERM:-} && $TERM != dumb && -z ${NO_COLOR:-} ]]; then
  bold=$'\033[1m'
  accent=$'\033[1;96m'
  good=$'\033[32m'
  reset=$'\033[0m'
fi
if [[ ${1:-} != --restart-internal ]]; then
  python3 "$here/scripts/update.py" recover || exit $?
  python3 "$here/scripts/backup.py" recover || exit $?
fi

panel_line() {
  # Fixed labels use ASCII and Chinese. Each Chinese character occupies two cells.
  local LC_ALL=C.UTF-8 text=$1 style=${2:-} chinese width
  chinese=${text//[ -~]/}
  width=$(( ${#text} + ${#chinese} ))
  printf '  │  %s%s%s%*s│\n' "$style" "$text" "$reset" "$((46-width))" ''
}

menu_item() {
  local label="[$1]  $2" chinese width LC_ALL=C.UTF-8
  chinese=${label//[ -~]/}
  width=$(( ${#label} + ${#chinese} ))
  printf '  │  %s[%s]%s  %s%*s│\n' "$accent" "$1" "$reset" "$2" "$((46-width))" ''
}

show_menu() {
  local status='未安装' status_style=$bold
  if [[ -f /etc/vps-dr/state.json ]]; then
    status='已安装 / 服务未运行'
    if ! command -v systemctl >/dev/null 2>&1; then
      status='服务状态不可用'
    elif systemctl is-active --quiet xray.service 2>/dev/null; then
      status='服务运行中'
      status_style=$good
    fi
  fi
  printf '\n  ┌────────────────────────────────────────────────┐\n'
  panel_line 'VPS DR    中文节点管理' "$bold"
  printf '  ├────────────────────────────────────────────────┤\n'
  panel_line "节点状态  $status" "$status_style"
  panel_line '节点协议  VMess / TCP 443'
  local bbr_status bbr_style=$bold
  bbr_status=$(python3 "$here/scripts/bbr.py" status 2>/dev/null) || bbr_status='状态不可用'
  [[ $bbr_status != 已开启 ]] || bbr_style=$good
  panel_line "BBR 状态  $bbr_status" "$bbr_style"
  printf '  ├────────────────────────────────────────────────┤\n'
  panel_line ''
  menu_item 1 '安装 / 继续安装'
  menu_item 2 '节点链接与二维码'
  menu_item 3 '检查运行状态'
  menu_item 4 '重启节点服务'
  menu_item 5 '检查更新 / 升级管理脚本'
  menu_item 6 '一键开启 BBR'
  menu_item 7 '流量统计'
  menu_item 8 '连接诊断'
  menu_item 9 '连接数与资源占用'
  menu_item 10 '备份节点配置'
  menu_item 11 '恢复节点配置'
  menu_item 12 'BBR 详情 / 恢复设置'
  menu_item 13 '查看近期故障日志'
  menu_item 14 '检查更新 / 升级 Xray'
  panel_line ''
  printf '  ├────────────────────────────────────────────────┤\n'
  panel_line '[0]  退出'
  printf '  └────────────────────────────────────────────────┘\n'
  printf '\n  %s请选择 [0-14] > %s' "$bold" "$reset"
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
  if [[ ${VPS_DR_LOCKED:-} != 1 ]] || ! python3 "$here/scripts/locked.py" check; then
    exec python3 "$here/scripts/locked.py" run bash "$here/menu.sh" --restart-internal
  fi
  python3 "$here/scripts/update.py" recover --lock-held || exit $?
  python3 "$here/scripts/backup.py" recover --lock-held || exit $?
  [[ -f /etc/vps-dr/state.json && -f /etc/systemd/system/xray.service ]] || {
    echo 'STATE_REQUIRED: 未发现已安装节点，请先选择 1。' >&2; exit 1;
  }
  bash "$here/preflight.sh" --local || exit $?
  systemctl restart xray.service || { echo 'RESTART_FAILED: 服务重启失败，请查看 systemd 日志。' >&2; exit 1; }
  bash "$here/health-check.sh"
)
if [[ ${1:-} == --restart-internal ]]; then restart_node; exit $?; fi
restore_node() {
  python3 "$here/scripts/backup.py" list || return $?
  echo '恢复会替换节点 UUID 和配置。当前配置将先自动备份，失败会尝试回滚。'
  printf '输入本机备份文件的绝对路径（直接回车取消）：'
  local backup_path answer
  read -r backup_path <&3 || return 1
  [[ -n $backup_path ]] || { echo '已取消恢复。'; return 0; }
  [[ $backup_path == /* ]] || { echo 'BACKUP_PATH_INVALID: 请填写绝对路径。' >&2; return 2; }
  printf '确认恢复请输入 RESTORE：'
  read -r answer <&3 || return 1
  [[ $answer == RESTORE ]] || { echo '已取消恢复。'; return 0; }
  python3 "$here/scripts/backup.py" restore "$backup_path" --confirm
}
manage_bbr() {
  python3 "$here/scripts/bbr.py" inspect || return $?
  echo '1 恢复首次开启前的设置   2 切换为 cubic   0 返回'
  echo '旧版开启且未保存历史时，无法推断原设置；切换 cubic 是单独操作。'
  local operation answer
  printf '请选择 [0-2]：'
  read -r operation <&3 || return 1
  case $operation in
    0|'') return 0 ;;
    1) operation=restore ;;
    2) operation=cubic ;;
    *) echo '请输入 0、1 或 2。' >&2; return 2 ;;
  esac
  printf '此操作会改变本机 TCP 默认算法，确认请输入 YES：'
  read -r answer <&3 || return 1
  [[ $answer == YES ]] || { echo '已取消。'; return 0; }
  python3 "$here/scripts/bbr.py" "$operation"
}
update_core() {
  python3 "$here/scripts/update.py" check || return $?
  echo '核心升级会短暂重启节点服务，保留 UUID 和配置，失败时尝试回滚。'
  local answer
  printf '升级到官方最新稳定版请输入 YES（直接回车返回）：'
  read -r answer <&3 || return 1
  [[ $answer == YES ]] || { echo '已取消核心升级。'; return 0; }
  python3 "$here/scripts/update.py" upgrade --confirm
}
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
      if python3 "$here/scripts/latest.py" management; then
        last_status=0
        if [[ -f /opt/vps-dr/menu.sh ]]; then exec bash /opt/vps-dr/menu.sh; fi
      else last_status=$?; fi
      ;;
    6)
      if python3 "$here/scripts/bbr.py" enable; then last_status=0; else last_status=$?; fi
      ;;
    7)
      if python3 "$here/scripts/traffic.py" show; then last_status=0; else last_status=$?; fi
      ;;
    8)
      if python3 "$here/scripts/observe.py" diagnostics; then last_status=0; else last_status=$?; fi
      ;;
    9)
      if python3 "$here/scripts/observe.py" connections; then last_status=0; else last_status=$?; fi
      ;;
    10)
      if python3 "$here/scripts/backup.py" backup; then last_status=0; else last_status=$?; fi
      ;;
    11)
      if restore_node; then last_status=0; else last_status=$?; fi
      ;;
    12)
      if manage_bbr; then last_status=0; else last_status=$?; fi
      ;;
    13)
      if python3 "$here/scripts/observe.py" logs; then last_status=0; else last_status=$?; fi
      ;;
    14)
      if update_core; then last_status=0; else last_status=$?; fi
      ;;
    0) exit "$last_status" ;;
    *) echo '请输入 0 到 14 之间的菜单编号。'; last_status=2; continue ;;
  esac
  if (( last_status == 0 )); then
    echo '操作完成。客户端实际连通性请在客户端测试。'
  else
    printf '操作未完成（退出码 %s），请根据上方提示处理后重试。\n' "$last_status" >&2
  fi
done
