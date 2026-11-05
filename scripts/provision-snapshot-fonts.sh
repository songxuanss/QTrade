#!/usr/bin/env bash
# Provision the byte-pinned snapshot font set into .tools/snapshot-fonts/ so
# Playwright pixel snapshots render from identical font bytes on the delivery
# host (Ubuntu 24 WSL) and on CI (ubuntu-24.04 runner).
#
# Idempotent: exits 0 immediately when the destination already verifies
# against apps/web/tests/snapshot-fonts.sha256. Font sources, in order:
#   1. the local .tools/browser-deps extraction (delivery host)
#   2. the pinned .deb files from archive.ubuntu.com (CI)
#   3. the same .deb files from snapshot.debian.org (permanent fallback)
# Every candidate is sha256-verified before the script succeeds; a mismatch
# fails loudly instead of silently drifting the snapshots.
#
# Network access honors the standard http_proxy/https_proxy environment
# variables (curl reads them natively).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/.tools/snapshot-fonts"
MANIFEST="$REPO_ROOT/apps/web/tests/snapshot-fonts.sha256"
BROWSER_DEPS_FONTS="$REPO_ROOT/.tools/browser-deps/root/usr/share/fonts"

NOTO_DEB="fonts-noto-cjk_20230817+repack1-3_all.deb"
LIB_DEB="fonts-liberation_2.1.5-3_all.deb"
NOTO_URL="https://archive.ubuntu.com/ubuntu/pool/main/f/fonts-noto-cjk/$NOTO_DEB"
LIB_URL="https://archive.ubuntu.com/ubuntu/pool/main/f/fonts-liberation/$LIB_DEB"
NOTO_FALLBACK_URL="https://snapshot.debian.org/archive/debian/20240101T000000Z/pool/main/f/fonts-noto-cjk/fonts-noto-cjk_20230817%2Brepack1-3_all.deb"
LIB_FALLBACK_URL="https://snapshot.debian.org/archive/debian/20240101T000000Z/pool/main/f/fonts-liberation/fonts-liberation_2.1.5-3_all.deb"

manifest_names() {
  grep -v '^#' "$MANIFEST" | grep -v '^[[:space:]]*$' | awk '{print $2}'
}

verify() {
  [ -d "$DEST" ] || return 1
  (cd "$DEST" && grep -v '^#' "$MANIFEST" | grep -v '^[[:space:]]*$' | sha256sum -c -) >/dev/null 2>&1
}

copy_manifest_fonts() { # $1 = font tree to search; missing files are skipped
  # because the manifest spans two trees (noto + liberation). verify() is the
  # gate that fails loudly when a file is absent from every source.
  local src="$1" name found
  mkdir -p "$DEST"
  while read -r name; do
    found="$(find "$src" -type f -name "$name" -print -quit)"
    if [ -n "$found" ]; then
      cp "$found" "$DEST/$name"
    fi
  done < <(manifest_names)
}

download_deb() { # $1 = primary url, $2 = fallback url, $3 = output file
  if ! curl -fsSL --retry 3 --retry-all-errors -C - -o "$3" "$1"; then
    echo "provision-snapshot-fonts: primary download failed, trying snapshot.debian.org" >&2
    curl -fsSL --retry 3 --retry-all-errors -C - -o "$3" "$2"
  fi
}

if verify; then
  echo "provision-snapshot-fonts: already provisioned and verified ($DEST)"
  exit 0
fi

if [ -d "$BROWSER_DEPS_FONTS" ]; then
  echo "provision-snapshot-fonts: copying from local browser-deps"
  if copy_manifest_fonts "$BROWSER_DEPS_FONTS" && verify; then
    echo "provision-snapshot-fonts: provisioned and verified ($DEST)"
    exit 0
  fi
  echo "provision-snapshot-fonts: browser-deps copy did not verify; falling back to pinned debs" >&2
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "provision-snapshot-fonts: downloading pinned debs"
download_deb "$NOTO_URL" "$NOTO_FALLBACK_URL" "$STAGING/$NOTO_DEB"
download_deb "$LIB_URL" "$LIB_FALLBACK_URL" "$STAGING/$LIB_DEB"
dpkg-deb -x "$STAGING/$NOTO_DEB" "$STAGING/noto"
dpkg-deb -x "$STAGING/$LIB_DEB" "$STAGING/lib"
copy_manifest_fonts "$STAGING/noto/usr/share/fonts"
copy_manifest_fonts "$STAGING/lib/usr/share/fonts"

if verify; then
  echo "provision-snapshot-fonts: provisioned and verified ($DEST)"
else
  echo "provision-snapshot-fonts: FAILED sha256 verification against $MANIFEST" >&2
  exit 1
fi
