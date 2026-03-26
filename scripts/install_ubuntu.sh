#!/usr/bin/env bash
set -euo pipefail

APP_NAME="scrobload"
SERVICE_NAME="${APP_NAME}.service"
TIMER_NAME="${APP_NAME}.timer"

INSTALL_DIR="/opt/scrobload"
ENV_FILE="/etc/scrobload.env"
OUTPUT_DIR="/var/lib/scrobload/downloads"
INTERVAL="15min"

RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"

LASTFM_USER=""
LIKED_ONLY="true"
PROVIDERS="spotify,ytmusic"
YTMUSIC_AUTH="headers_auth.json"
LIMIT="50"

usage() {
  cat <<USAGE
Usage: sudo ./scripts/install_ubuntu.sh [options]

Options:
  --user USER               Linux user that should run scrobload (default: ${RUN_USER})
  --install-dir PATH        Install directory (default: ${INSTALL_DIR})
  --env-file PATH           Environment file path (default: ${ENV_FILE})
  --output-dir PATH         Download output dir (default: ${OUTPUT_DIR})
  --interval DURATION       systemd timer interval, e.g. 15min, 1h (default: ${INTERVAL})
  --lastfm-user USERNAME    Last.fm username (required)
  --providers LIST          liked providers CSV (default: ${PROVIDERS})
  --ytmusic-auth PATH       Path to ytmusic auth json relative to install dir (default: ${YTMUSIC_AUTH})
  --limit N                 Number of recent scrobbles to inspect each run (default: ${LIMIT})
  --all-scrobbles           Disable liked-only filtering
  -h, --help                Show this help

After installation, edit ${ENV_FILE} to set API keys/secrets.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      RUN_USER="$2"; RUN_GROUP="$(id -gn "$RUN_USER")"; shift 2;;
    --install-dir)
      INSTALL_DIR="$2"; shift 2;;
    --env-file)
      ENV_FILE="$2"; shift 2;;
    --output-dir)
      OUTPUT_DIR="$2"; shift 2;;
    --interval)
      INTERVAL="$2"; shift 2;;
    --lastfm-user)
      LASTFM_USER="$2"; shift 2;;
    --providers)
      PROVIDERS="$2"; shift 2;;
    --ytmusic-auth)
      YTMUSIC_AUTH="$2"; shift 2;;
    --limit)
      LIMIT="$2"; shift 2;;
    --all-scrobbles)
      LIKED_ONLY="false"; shift;;
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
  echo "Please run as root (e.g. sudo ./scripts/install_ubuntu.sh ...)." >&2
  exit 1
fi

if [[ -z "$LASTFM_USER" ]]; then
  echo "--lastfm-user is required." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

echo "[1/7] Installing OS packages..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip

echo "[2/7] Creating app directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$OUTPUT_DIR"

echo "[3/7] Copying project files to $INSTALL_DIR ..."
install -m 644 "$REPO_DIR/app.py" "$INSTALL_DIR/app.py"
install -m 644 "$REPO_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
install -m 644 "$REPO_DIR/README.md" "$INSTALL_DIR/README.md"

if [[ -f "$REPO_DIR/headers_auth.json" && ! -f "$INSTALL_DIR/headers_auth.json" ]]; then
  install -m 600 "$REPO_DIR/headers_auth.json" "$INSTALL_DIR/headers_auth.json"
fi

echo "[4/7] Preparing virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$(dirname "$OUTPUT_DIR")"
chown -R "$RUN_USER:$RUN_GROUP" "$OUTPUT_DIR"

SCROBLOAD_ARGS="--lastfm-user ${LASTFM_USER} --limit ${LIMIT} --output-dir ${OUTPUT_DIR}"
if [[ "$LIKED_ONLY" == "true" ]]; then
  SCROBLOAD_ARGS+=" --liked-only --providers ${PROVIDERS} --ytmusic-auth ${YTMUSIC_AUTH}"
fi

echo "[5/7] Creating environment file: $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF_ENV
# Required:
LASTFM_API_KEY=

# Spotify (needed if providers include spotify):
SPOTIPY_CLIENT_ID=
SPOTIPY_CLIENT_SECRET=
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback

# App args passed to app.py by systemd:
SCROBLOAD_ARGS="${SCROBLOAD_ARGS}"
EOF_ENV
  chmod 600 "$ENV_FILE"
fi

echo "[6/7] Writing systemd unit files..."
cat > "/etc/systemd/system/${SERVICE_NAME}" <<EOF_SERVICE
[Unit]
Description=Scrobload (fetch Last.fm scrobbles and download tracks)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash -lc '${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/app.py \$SCROBLOAD_ARGS'

[Install]
WantedBy=multi-user.target
EOF_SERVICE

cat > "/etc/systemd/system/${TIMER_NAME}" <<EOF_TIMER
[Unit]
Description=Run Scrobload periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}
Persistent=true
Unit=${SERVICE_NAME}

[Install]
WantedBy=timers.target
EOF_TIMER

echo "[7/7] Enabling timer..."
systemctl daemon-reload
systemctl enable --now "$TIMER_NAME"

echo
echo "Install complete."
echo "1) Edit credentials in: ${ENV_FILE}"
echo "2) Run a test job: sudo systemctl start ${SERVICE_NAME}"
echo "3) Check status: sudo systemctl status ${TIMER_NAME}"
echo "4) View logs: sudo journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
