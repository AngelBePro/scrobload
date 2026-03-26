#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

VERSION="$(tr -d '[:space:]' <"${REPO_DIR}/VERSION")"
PKG_NAME="scrobload"
PKGREL="1"
ARCH="any"
OUT_DIR="${REPO_DIR}/dist"
BUILD_ROOT="${REPO_DIR}/.build/arch"
PKG_ROOT="${BUILD_ROOT}/${PKG_NAME}-${VERSION}"
MAKEPKG_ROOT="${BUILD_ROOT}/makepkg"

rm -rf "${PKG_ROOT}" "${MAKEPKG_ROOT}"
mkdir -p "${PKG_ROOT}/usr/lib/scrobload"
mkdir -p "${PKG_ROOT}/usr/bin"
mkdir -p "${PKG_ROOT}/usr/lib/systemd/system"

install -m 644 "${REPO_DIR}/app.py" "${PKG_ROOT}/usr/lib/scrobload/app.py"
install -m 644 "${REPO_DIR}/requirements.txt" "${PKG_ROOT}/usr/lib/scrobload/requirements.txt"
install -m 755 "${REPO_DIR}/scripts/scrobload" "${PKG_ROOT}/usr/bin/scrobload"
install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.service" "${PKG_ROOT}/usr/lib/systemd/system/scrobload.service"
install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.timer" "${PKG_ROOT}/usr/lib/systemd/system/scrobload.timer"

cat >"${PKG_ROOT}/.PKGINFO" <<EOF_PKGINFO
pkgname = ${PKG_NAME}
pkgbase = ${PKG_NAME}
pkgver = ${VERSION}-${PKGREL}
pkgdesc = Download recent Last.fm scrobbles (optionally only liked songs)
url = https://example.com/scrobload
builddate = $(date +%s)
packager = Scrobload Maintainers <maintainers@example.com>
size = $(du -sb "${PKG_ROOT}" | awk '{print $1}')
arch = ${ARCH}
license = GPL2
depend = bash
depend = python
depend = python-pip
depend = python-virtualenv
depend = systemd
EOF_PKGINFO

cp "${REPO_DIR}/packaging/arch/scrobload.install" "${PKG_ROOT}/.INSTALL"

mkdir -p "${OUT_DIR}"
PKG_FILE="${OUT_DIR}/${PKG_NAME}-${VERSION}-${PKGREL}-${ARCH}.pkg.tar.zst"

if command -v makepkg >/dev/null 2>&1; then
  mkdir -p "${MAKEPKG_ROOT}"
  sed "s/__VERSION__/${VERSION}/g" "${REPO_DIR}/packaging/arch/PKGBUILD.in" >"${MAKEPKG_ROOT}/PKGBUILD"
  install -m 644 "${REPO_DIR}/app.py" "${MAKEPKG_ROOT}/app.py"
  install -m 644 "${REPO_DIR}/requirements.txt" "${MAKEPKG_ROOT}/requirements.txt"
  install -m 755 "${REPO_DIR}/scripts/scrobload" "${MAKEPKG_ROOT}/scrobload"
  install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.service" "${MAKEPKG_ROOT}/scrobload.service"
  install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.timer" "${MAKEPKG_ROOT}/scrobload.timer"
  install -m 644 "${REPO_DIR}/packaging/arch/scrobload.install" "${MAKEPKG_ROOT}/scrobload.install"

  (
    cd "${MAKEPKG_ROOT}"
    makepkg -f --nodeps --noconfirm
  )

  mv "${MAKEPKG_ROOT}"/*.pkg.tar.zst "${OUT_DIR}/"
  echo "Built with makepkg:"
  ls -1 "${OUT_DIR}"/*.pkg.tar.zst
else
  tar --zstd -cf "${PKG_FILE}" -C "${PKG_ROOT}" .
  echo "Built fallback package (makepkg not found): ${PKG_FILE}"
  echo "Note: for full PKGBUILD-driven builds, run this script on Arch with makepkg installed."
fi
