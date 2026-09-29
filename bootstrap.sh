#!/usr/bin/env bash
set -Eeuo pipefail

# The eventual release entry point defaults to its immutable tag. Until the
# validation gate passes, development use must explicitly opt in to main.
repo="sweet0416/vps-dr"
ref="${VPS_DR_REF:-v1.0.0}"
[[ "$ref" == main || "$ref" == v1.0.0 ]] || { echo "Unsupported source ref." >&2; exit 1; }
if (( EUID != 0 )); then
  echo "Run as root (sudo bash bootstrap.sh)." >&2
  exit 1
fi
command -v curl >/dev/null || { echo "curl is required (apt-get install curl)." >&2; exit 1; }
command -v tar >/dev/null || { echo "tar is required (apt-get install tar)." >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if [[ "$ref" == main ]]; then archive="heads/main"; else archive="tags/$ref"; fi
curl -fsSL --retry 3 "https://github.com/${repo}/archive/refs/${archive}.tar.gz" -o "$tmp/repo.tar.gz"
source_dir="$tmp/source"
mkdir "$source_dir"
tar -xzf "$tmp/repo.tar.gz" --strip-components=1 -C "$source_dir"
[[ -f "$source_dir/VERSION" ]] || { echo "VERSION missing from source archive." >&2; exit 1; }
version=$(<"$source_dir/VERSION")
if [[ "$ref" != main && "v$version" != "$ref" ]]; then echo "Release tag and VERSION disagree." >&2; exit 1; fi
printf 'VPS_DR_VERSION: %s\nSOURCE_REF: %s\n' "$version" "$ref"
bash "$source_dir/install.sh" "$@"
