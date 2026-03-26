#!/usr/bin/env bash
set -euo pipefail

APP_NAME="scrobload"
SERVICE_NAME="${APP_NAME}.service"
TIMER_NAME="${APP_NAME}.timer"

INSTALL_DIR="/opt/scrobload"
ENV_FILE="/etc/scrobload.env"
PURGE_DOWNLOADS="false"

usage() {
  cat <<USAGE
Usage: sudo ./scripts/uninstall_ubuntu.sh [options]

Options:
  --install-dir PATH     Install directory to remove (default: ${INSTALL_DIR})
  --env-file PATH        Environment file to remove (default: ${ENV_FILE})
  --purge-downloads      Also remove downloaded files under /var/lib/scrobload
  -h, --help             Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="$2"; shift 2;;
    --env-file)
      ENV_FILE="$2"; shift 2;;
    --purge-downloads)
      PURGE_DOWNLOADS="true"; shift;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (e.g. sudo ./scripts/uninstall_ubuntu.sh)." >&2
  exit 1
fi

echo "Stopping/disabling timer and service (if present)..."
systemctl stop "$TIMER_NAME" 2>/dev/null || true
systemctl disable "$TIMER_NAME" 2>/dev/null || true
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true

echo "Removing systemd unit files..."
rm -f "/etc/systemd/system/${SERVICE_NAME}"
rm -f "/etc/systemd/system/${TIMER_NAME}"
systemctl daemon-reload

echo "Removing install dir: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

if [[ -f "$ENV_FILE" ]]; then
  echo "Removing env file: $ENV_FILE"
  rm -f "$ENV_FILE"
fi

if [[ "$PURGE_DOWNLOADS" == "true" ]]; then
  echo "Purging /var/lib/scrobload ..."
  rm -rf /var/lib/scrobload
else
  echo "Keeping downloaded files in /var/lib/scrobload (use --purge-downloads to remove)."
fi

echo "Uninstall complete."
