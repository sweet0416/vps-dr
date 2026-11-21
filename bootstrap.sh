#!/usr/bin/env bash
set -Eeuo pipefail
(( EUID == 0 )) || { echo "请在 VPS 的 root 终端运行此命令。" >&2; exit 1; }

# A checked-out file runs locally. Piped raw content downloads a chosen ref.
source_file=${BASH_SOURCE[0]:-}
if [[ "${source_file##*/}" == bootstrap.sh && -f "$source_file" ]]; then
  here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  [[ -f "$here/VERSION" && -f "$here/install.sh" ]] || { echo "本地项目文件不完整。" >&2; exit 1; }
  printf 'VPS_DR_VERSION: %s\nSOURCE: local checkout\n' "$(<"$here/VERSION")"
  if (( $# == 0 )); then exec bash "$here/menu.sh"; fi
  exec bash "$here/install.sh" "$@"
fi

repo=sweet0416/vps-dr
ref=${VPS_DR_REF:-main}
[[ "$ref" == main || "$ref" == v1.1.0 || "$ref" == v1.2.0 ]] || { echo "不支持此来源版本，请使用 main、v1.1.0 或 v1.2.0。" >&2; exit 1; }
command -v curl >/dev/null && command -v tar >/dev/null || { echo "需要安装 curl 和 tar。" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
if [[ "$ref" == main ]]; then archive=heads/main; else archive="tags/$ref"; fi
curl -fsSL --connect-timeout 15 --max-time 180 --retry 2 "https://github.com/$repo/archive/refs/$archive.tar.gz" -o "$tmp/repo.tar.gz"
mkdir "$tmp/source"
tar -xzf "$tmp/repo.tar.gz" --strip-components=1 -C "$tmp/source"
[[ -f "$tmp/source/VERSION" ]] || { echo "下载内容缺少 VERSION，请重试。" >&2; exit 1; }
version=$(<"$tmp/source/VERSION")
if [[ "$ref" != main && "v$version" != "$ref" ]]; then echo "标签与版本不一致，已停止。" >&2; exit 1; fi
printf 'VPS_DR_VERSION: %s\nSOURCE_REF: %s\n' "$version" "$ref"
if (( $# == 0 )); then
  bash "$tmp/source/menu.sh"
else
  bash "$tmp/source/install.sh" "$@"
fi
