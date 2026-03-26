# Scrobload

Scrobload fetches your latest Last.fm scrobbles and downloads the tracks.

It can also run in **liked-only** mode, where it downloads only tracks that are liked in:
- Spotify
- YouTube Music

## 1) Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Basic run:

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --lastfm-api-key "$LASTFM_API_KEY" \
  --limit 30
```

Liked-only run:

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --liked-only \
  --providers spotify,ytmusic \
  --ytmusic-auth headers_auth.json
```

Dry run:

```bash
python app.py --lastfm-user YOUR_LASTFM_USERNAME --liked-only --dry-run
```

Background/daemon mode (continuous polling):

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --liked-only \
  --daemon \
  --poll-interval 900
```

Download location control:

- CLI parameter: `--output-dir /path/to/downloads`
- Environment variable: `SCROBLOAD_OUTPUT_DIR=/path/to/downloads`

Delete protection (enabled by default):

- If you manually delete a previously downloaded track file, Scrobload remembers that and will **not** download it again.
- To disable this behavior and allow re-downloading deleted tracks, pass: `--redownload-deleted`

Example:

```bash
SCROBLOAD_OUTPUT_DIR="/srv/music/scrobload" python app.py --lastfm-user YOUR_LASTFM_USERNAME
```

---

## 2) Credentials

### Last.fm
- Create/get API key: https://www.last.fm/api/account/create
- Set env var or pass via CLI:

```bash
export LASTFM_API_KEY="your_lastfm_api_key"
```

### Optional app env vars

```bash
export SCROBLOAD_OUTPUT_DIR="/path/to/downloads"
```

### Spotify (only if using provider `spotify`)

```bash
export SPOTIPY_CLIENT_ID="your_client_id"
export SPOTIPY_CLIENT_SECRET="your_client_secret"
export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
```

### YouTube Music (only if using provider `ytmusic`)
- Generate `headers_auth.json` with ytmusicapi setup:
  https://ytmusicapi.readthedocs.io/en/latest/setup/browser.html

---

## 3) Ubuntu server install (systemd)

Install:

```bash
chmod +x scripts/install_ubuntu.sh scripts/uninstall_ubuntu.sh
sudo ./scripts/install_ubuntu.sh --lastfm-user YOUR_LASTFM_USERNAME
```

Useful flags:
- `--interval 15min`
- `--all-scrobbles`
- `--providers spotify,ytmusic`
- `--ytmusic-auth headers_auth.json`

To run as a long-running background process with systemd,
set daemon args in `/etc/scrobload.env`, e.g.:

```bash
SCROBLOAD_ARGS="--lastfm-user YOUR_LASTFM_USERNAME --liked-only --daemon --poll-interval 900 --output-dir /var/lib/scrobload/downloads"
```

After install:

```bash
sudo systemctl start scrobload.service
sudo systemctl status scrobload.timer
sudo journalctl -u scrobload.service -n 100 --no-pager
```

Uninstall:

```bash
sudo ./scripts/uninstall_ubuntu.sh
sudo ./scripts/uninstall_ubuntu.sh --purge-downloads
```

---

## 4) Packaging

Build both package formats from this repo:
- Debian: `.deb`
- Arch: `.pkg.tar.zst` (PKGBUILD-compatible)

```bash
chmod +x scripts/build_deb.sh scripts/build_arch_pkg.sh scripts/release_packages.sh
```

Build Debian package:

```bash
./scripts/build_deb.sh
```

Build Arch package:

```bash
./scripts/build_arch_pkg.sh
```

Build both:

```bash
./scripts/release_packages.sh
```

PKGBUILD assets:
- `packaging/arch/PKGBUILD.in`
- `packaging/arch/scrobload.install`

---

## Notes

- Matching is best-effort (`artist + title` normalization).
- Downloads come from YouTube search results (`ytsearch1`), so exact versions can vary.
- Download tracking metadata is stored in `OUTPUT_DIR/.scrobload_state.json`.
- Use responsibly and in line with your local laws/platform terms.
