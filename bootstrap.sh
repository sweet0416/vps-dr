#!/usr/bin/env bash
set -Eeuo pipefail

# Public entry point. This script only downloads this repository and runs install.sh.
repo="sweet0416/vps-dr"
branch="main"
if (( EUID != 0 )); then
  echo "Run as root: curl -fsSL https://raw.githubusercontent.com/${repo}/${branch}/bootstrap.sh | sudo bash" >&2
  exit 1
fi
command -v curl >/dev/null || { echo "curl is required (apt-get install curl)." >&2; exit 1; }
command -v tar >/dev/null || { echo "tar is required (apt-get install tar)." >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 "https://github.com/${repo}/archive/refs/heads/${branch}.tar.gz" -o "$tmp/repo.tar.gz"
tar -xzf "$tmp/repo.tar.gz" -C "$tmp"
bash "$tmp/vps-dr-${branch}/install.sh" "$@"
