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
