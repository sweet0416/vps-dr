#!/usr/bin/env bash
set -Eeuo pipefail
(( EUID == 0 )) || { echo "Run as root: sudo ./bootstrap.sh" >&2; exit 1; }

# A checked-out file runs locally. Piped raw content downloads a chosen ref.
if [[ "${BASH_SOURCE[0]##*/}" == bootstrap.sh && -f "${BASH_SOURCE[0]}" ]]; then
  here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  [[ -f "$here/VERSION" && -f "$here/install.sh" ]] || { echo "Incomplete vps-dr checkout." >&2; exit 1; }
  printf 'VPS_DR_VERSION: %s\nSOURCE: local checkout\n' "$(<"$here/VERSION")"
  exec bash "$here/install.sh" "$@"
fi

repo=sweet0416/vps-dr
ref=${VPS_DR_REF:-v1.0.0}
[[ "$ref" == main || "$ref" == v1.0.0 ]] || { echo "Unsupported source ref." >&2; exit 1; }
command -v curl >/dev/null && command -v tar >/dev/null || { echo "curl and tar are required." >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
if [[ "$ref" == main ]]; then archive=heads/main; else archive="tags/$ref"; fi
curl -fsSL --retry 3 "https://github.com/$repo/archive/refs/$archive.tar.gz" -o "$tmp/repo.tar.gz"
mkdir "$tmp/source"
tar -xzf "$tmp/repo.tar.gz" --strip-components=1 -C "$tmp/source"
[[ -f "$tmp/source/VERSION" ]] || { echo "VERSION missing from archive." >&2; exit 1; }
version=$(<"$tmp/source/VERSION")
if [[ "$ref" != main && "v$version" != "$ref" ]]; then echo "Tag and VERSION disagree." >&2; exit 1; fi
printf 'VPS_DR_VERSION: %s\nSOURCE_REF: %s\n' "$version" "$ref"
bash "$tmp/source/install.sh" "$@"
