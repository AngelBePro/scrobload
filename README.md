# Scrobload

A small CLI app that:

1. Reads your latest scrobbles from Last.fm
2. Optionally filters them to only tracks you have liked in Spotify and/or YouTube Music
3. Downloads matching audio using `yt-dlp`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ubuntu server install (easy 24/7)

This repo includes scripts for system-wide install + uninstall using systemd.

### Install

From this repo directory:

```bash
chmod +x scripts/install_ubuntu.sh scripts/uninstall_ubuntu.sh
sudo ./scripts/install_ubuntu.sh --lastfm-user YOUR_LASTFM_USERNAME
```

Optional install flags:

- `--interval 15min` (or `1h`, etc.)
- `--all-scrobbles` (disable liked-only mode)
- `--providers spotify,ytmusic`
- `--ytmusic-auth headers_auth.json`

After install:

1. Edit credentials in `/etc/scrobload.env`
2. Run test job:

```bash
sudo systemctl start scrobload.service
```

3. Check timer:

```bash
sudo systemctl status scrobload.timer
```

4. See logs:

```bash
sudo journalctl -u scrobload.service -n 100 --no-pager
```

### Uninstall

```bash
sudo ./scripts/uninstall_ubuntu.sh
```

Also delete downloaded files:

```bash
sudo ./scripts/uninstall_ubuntu.sh --purge-downloads
```

## Packaging / release artifacts

You can build both package formats from this repo:

- Debian: `.deb`
- Arch: `.pkg.tar.zst` (PKGBUILD-compatible)

Make scripts executable first:

```bash
chmod +x scripts/build_deb.sh scripts/build_arch_pkg.sh scripts/release_packages.sh
```

### Build .deb

```bash
./scripts/build_deb.sh
```

Output:

- `dist/scrobload_<version>_all.deb`

### Build Arch package

```bash
./scripts/build_arch_pkg.sh
```

Behavior:

- If `makepkg` is available, it builds from `packaging/arch/PKGBUILD.in` (rendered with current `VERSION`)
- If `makepkg` is not available, it creates a fallback `.pkg.tar.zst` archive

Output:

- `dist/scrobload-<version>-1-any.pkg.tar.zst`

### Build both artifacts

```bash
./scripts/release_packages.sh
```

### PKGBUILD files

- Template: `packaging/arch/PKGBUILD.in`
- Install hooks: `packaging/arch/scrobload.install`

On Arch, you can also copy these into a packaging directory and run `makepkg -f` directly.

## Required credentials

### Last.fm

- Create/get an API key: https://www.last.fm/api/account/create
- Pass it via `--lastfm-api-key` or env var:

```bash
export LASTFM_API_KEY="your_lastfm_api_key"
```

### Spotify (only if using `--liked-only` with spotify provider)

Create a Spotify app and set:

```bash
export SPOTIPY_CLIENT_ID="your_client_id"
export SPOTIPY_CLIENT_SECRET="your_client_secret"
export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
```

The first run opens an auth flow for `user-library-read`.

### YouTube Music (only if using `--liked-only` with ytmusic provider)

Generate auth headers with ytmusicapi and save as `headers_auth.json` (or pass a custom path using `--ytmusic-auth`).

Reference: https://ytmusicapi.readthedocs.io/en/latest/setup/browser.html

## Usage

### Download latest scrobbles

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --lastfm-api-key "$LASTFM_API_KEY" \
  --limit 30
```

### Download only liked songs (Spotify + YouTube Music)

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --liked-only \
  --providers spotify,ytmusic \
  --ytmusic-auth headers_auth.json
```

### Dry run (show what would be downloaded)

```bash
python app.py \
  --lastfm-user YOUR_LASTFM_USERNAME \
  --liked-only \
  --dry-run
```

## Notes

- Matching is best-effort (normalized `artist + title` text).
- Downloads are sourced from YouTube search results (`ytsearch1`) so exact versions may vary.
- Use responsibly and in accordance with local laws + platform terms.
