#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

VERSION="$(tr -d '[:space:]' <"${REPO_DIR}/VERSION")"
PKG_NAME="scrobload"
ARCH="all"
OUT_DIR="${REPO_DIR}/dist"
BUILD_ROOT="${REPO_DIR}/.build/deb"
PKG_DIR="${BUILD_ROOT}/${PKG_NAME}_${VERSION}_${ARCH}"

rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/lib/scrobload"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/lib/systemd/system"

install -m 644 "${REPO_DIR}/app.py" "${PKG_DIR}/usr/lib/scrobload/app.py"
install -m 644 "${REPO_DIR}/requirements.txt" "${PKG_DIR}/usr/lib/scrobload/requirements.txt"
install -m 755 "${REPO_DIR}/scripts/scrobload" "${PKG_DIR}/usr/bin/scrobload"
install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.service" "${PKG_DIR}/lib/systemd/system/scrobload.service"
install -m 644 "${REPO_DIR}/packaging/systemd/scrobload.timer" "${PKG_DIR}/lib/systemd/system/scrobload.timer"

sed "s/__VERSION__/${VERSION}/g" "${REPO_DIR}/packaging/debian/control" >"${PKG_DIR}/DEBIAN/control"
install -m 755 "${REPO_DIR}/packaging/debian/preinst" "${PKG_DIR}/DEBIAN/preinst"
install -m 755 "${REPO_DIR}/packaging/debian/postinst" "${PKG_DIR}/DEBIAN/postinst"
install -m 755 "${REPO_DIR}/packaging/debian/prerm" "${PKG_DIR}/DEBIAN/prerm"
install -m 755 "${REPO_DIR}/packaging/debian/postrm" "${PKG_DIR}/DEBIAN/postrm"

mkdir -p "${OUT_DIR}"
DEB_FILE="${OUT_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

if command -v dpkg-deb >/dev/null 2>&1; then
  dpkg-deb --build "${PKG_DIR}" "${DEB_FILE}"
  echo "Built with dpkg-deb: ${DEB_FILE}"
else
  echo "dpkg-deb not found; building fallback .deb via ar/tar"
  TMP_DEB_DIR="${BUILD_ROOT}/deb_fallback"
  rm -rf "${TMP_DEB_DIR}"
  mkdir -p "${TMP_DEB_DIR}"

  echo "2.0" >"${TMP_DEB_DIR}/debian-binary"

  (
    cd "${PKG_DIR}/DEBIAN"
    tar -czf "${TMP_DEB_DIR}/control.tar.gz" .
  )

  (
    cd "${PKG_DIR}"
    tar \
      --exclude='./DEBIAN' \
      -czf "${TMP_DEB_DIR}/data.tar.gz" .
  )

  if ! command -v ar >/dev/null 2>&1; then
    echo "Error: neither dpkg-deb nor ar is available to build a .deb package." >&2
    exit 1
  fi

  (
    cd "${TMP_DEB_DIR}"
    ar r "${DEB_FILE}" debian-binary control.tar.gz data.tar.gz >/dev/null
  )

  echo "Built fallback .deb: ${DEB_FILE}"
fi
