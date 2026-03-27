#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import importlib
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Iterable, Sequence

import requests


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording/"
MUSICBRAINZ_USER_AGENT = "scrobload/1.0 (https://github.com/AngelBePro/scrobload)"
DEFAULT_OUTPUT_DIR = os.getenv("SCROBLOAD_OUTPUT_DIR", "downloads")
STATE_FILE_NAME = ".scrobload_state.json"


def require_package(import_name: str, package_name: str):
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Missing dependency '{package_name}'. Install requirements with: pip install -r requirements.txt"
        ) from exc


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    album: str | None = None
    year: str | None = None
    track_number: int | None = None
    genre: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (normalize_text(self.artist), normalize_text(self.title))

    @property
    def query(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def key_str(self) -> str:
        artist, title = self.key
        return f"{artist}||{title}"


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\(feat\.?[^)]*\)", "", value)
    value = re.sub(r"\[feat\.?[^]]*\]", "", value)
    value = re.sub(r"\b(feat\.?|ft\.?)\b.*$", "", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fetch_recent_scrobbles(user: str, api_key: str, limit: int, unique: bool) -> list[Track]:
    params = {
        "method": "user.getrecenttracks",
        "user": user,
        "api_key": api_key,
        "format": "json",
        "limit": limit,
        "extended": 0,
    }

    response = requests.get(LASTFM_API_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise RuntimeError(f"Last.fm API error {payload['error']}: {payload.get('message', 'unknown error')}")

    raw_tracks = payload.get("recenttracks", {}).get("track", [])
    tracks: list[Track] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_tracks:
        artist = item.get("artist", {}).get("#text", "").strip()
        title = item.get("name", "").strip()
        album = item.get("album", {}).get("#text", "").strip() or None

        if not artist or not title:
            continue

        track = Track(title=title, artist=artist, album=album)
        if unique:
            if track.key in seen:
                continue
            seen.add(track.key)
        tracks.append(track)

    return tracks


def fetch_metadata_from_musicbrainz(artist: str, title: str) -> dict:
    """Look up album, year, track number, and genre from MusicBrainz."""
    metadata: dict = {}
    try:
        # Query MusicBrainz for the recording
        query = f'artist:"{artist}" AND recording:"{title}"'
        params = {
            "query": query,
            "fmt": "json",
            "limit": 1,
        }
        headers = {"User-Agent": MUSICBRAINZ_USER_AGENT}
        response = requests.get(MUSICBRAINZ_API_URL, params=params, headers=headers, timeout=15)
        if response.status_code != 200:
            return metadata

        payload = response.json()
        recordings = payload.get("recordings", [])
        if not recordings:
            return metadata

        recording = recordings[0]

        # Get album from release list
        releases = recording.get("releases", [])
        if releases:
            release = releases[0]
            album_name = release.get("title", "").strip()
            if album_name:
                metadata["album"] = album_name

            # Get year from release date
            date = release.get("date", "").strip()
            if date:
                year_match = re.match(r"^(\d{4})", date)
                if year_match:
                    metadata["year"] = year_match.group(1)

            # Get track number
            media = release.get("media", [])
            if media:
                tracks_list = media[0].get("tracks", [])
                if tracks_list:
                    track_num = tracks_list[0].get("number")
                    if track_num and track_num.isdigit():
                        metadata["track_number"] = int(track_num)

        # Get genre from tags
        tags = recording.get("tags", [])
        if tags:
            # Sort by count (highest first) and take the top genre
            sorted_tags = sorted(tags, key=lambda t: t.get("count", 0), reverse=True)
            top_tag = sorted_tags[0].get("name", "").strip()
            if top_tag:
                metadata["genre"] = top_tag

    except Exception:
        pass

    return metadata


def extract_youtube_metadata(ydl_result: dict) -> dict:
    """Extract useful metadata from a yt-dlp download result."""
    metadata: dict = {}

    if not isinstance(ydl_result, dict):
        return metadata

    # Try to extract album from YouTube video metadata
    album = ydl_result.get("album")
    if album:
        metadata["album"] = str(album).strip()

    # Extract from description if structured (some music videos have metadata in description)
    description = ydl_result.get("description", "")
    if description:
        # Look for common patterns in music video descriptions
        album_match = re.search(r"(?:Album|Álbum|EP)[:\s]+(.+?)(?:\n|$)", description, re.IGNORECASE)
        if album_match and "album" not in metadata:
            album_text = album_match.group(1).strip()
            if album_text and len(album_text) < 200:
                metadata["album"] = album_text

        # Try to extract genre from description
        genre_match = re.search(r"(?:Genre|Género)[:\s]+(.+?)(?:\n|$)", description, re.IGNORECASE)
        if genre_match:
            genre_text = genre_match.group(1).strip()
            if genre_text and len(genre_text) < 100:
                metadata["genre"] = genre_text

    # Extract from channel/uploader as album artist hint
    uploader = ydl_result.get("uploader", "")
    if uploader:
        metadata["youtube_uploader"] = uploader

    return metadata


def enrich_track_metadata(track: Track, youtube_metadata: dict | None = None, use_musicbrainz: bool = True) -> Track:
    """Enrich a track with metadata from multiple sources.
    
    Priority: Track's existing data > YouTube metadata > MusicBrainz metadata
    """
    updates: dict = {}

    # Start with YouTube metadata as fallback
    if youtube_metadata:
        if not track.album and youtube_metadata.get("album"):
            updates["album"] = youtube_metadata["album"]
        if not track.genre and youtube_metadata.get("genre"):
            updates["genre"] = youtube_metadata["genre"]

    # Try MusicBrainz for missing metadata
    if use_musicbrainz:
        needs_mb = not track.album or not track.year or not track.track_number or not track.genre
        # Only if we still have gaps after YouTube metadata
        if needs_mb or not updates.get("album"):
            mb_meta = fetch_metadata_from_musicbrainz(track.artist, track.title)
            if mb_meta:
                if not track.album and not updates.get("album") and mb_meta.get("album"):
                    updates["album"] = mb_meta["album"]
                if not track.year and mb_meta.get("year"):
                    updates["year"] = mb_meta["year"]
                if not track.track_number and mb_meta.get("track_number"):
                    updates["track_number"] = mb_meta["track_number"]
                if not track.genre and not updates.get("genre") and mb_meta.get("genre"):
                    updates["genre"] = mb_meta["genre"]

    if updates:
        return replace(track, **updates)
    return track


def load_spotify_likes(limit: int | None = None) -> set[tuple[str, str]]:
    spotipy_module = require_package("spotipy", "spotipy")
    oauth_module = require_package("spotipy.oauth2", "spotipy")
    Spotify = spotipy_module.Spotify
    SpotifyOAuth = oauth_module.SpotifyOAuth

    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Spotify provider selected but SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET is missing."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope="user-library-read",
    )

    sp = Spotify(auth_manager=auth_manager)

    likes: set[tuple[str, str]] = set()
    offset = 0
    page_size = 50

    while True:
        page = sp.current_user_saved_tracks(limit=page_size, offset=offset)
        items = page.get("items", [])
        if not items:
            break

        for item in items:
            track_obj = item.get("track") or {}
            title = (track_obj.get("name") or "").strip()
            artists = track_obj.get("artists") or []
            artist = (artists[0].get("name") if artists else "") or ""
            artist = artist.strip()
            if title and artist:
                likes.add((normalize_text(artist), normalize_text(title)))

        offset += len(items)
        if limit is not None and offset >= limit:
            break

    return likes


def load_ytmusic_likes(auth_file: str, limit: int = 5000) -> set[tuple[str, str]]:
    ytmusic_module = require_package("ytmusicapi", "ytmusicapi")
    YTMusic = ytmusic_module.YTMusic

    ytmusic = YTMusic(auth_file)
    liked = ytmusic.get_liked_songs(limit=limit)
    tracks = liked.get("tracks") or []

    likes: set[tuple[str, str]] = set()
    for item in tracks:
        title = (item.get("title") or "").strip()
        artists = item.get("artists") or []
        artist = (artists[0].get("name") if artists else "") or ""
        artist = artist.strip()
        if title and artist:
            likes.add((normalize_text(artist), normalize_text(title)))

    return likes


def build_liked_index(providers: Sequence[str], ytmusic_auth: str) -> set[tuple[str, str]]:
    liked: set[tuple[str, str]] = set()
    for provider in providers:
        if provider == "spotify":
            provider_likes = load_spotify_likes()
            liked |= provider_likes
            print(f"[likes] loaded {len(provider_likes)} liked tracks from Spotify")
        elif provider == "ytmusic":
            provider_likes = load_ytmusic_likes(ytmusic_auth)
            liked |= provider_likes
            print(f"[likes] loaded {len(provider_likes)} liked tracks from YouTube Music")
        else:
            raise RuntimeError(f"Unsupported provider: {provider}")

    return liked


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {"downloads": {}}

    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"downloads": {}}

    if not isinstance(payload, dict):
        return {"downloads": {}}

    downloads = payload.get("downloads")
    if not isinstance(downloads, dict):
        payload["downloads"] = {}

    return payload


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def apply_metadata_tags(file_path: Path, track: Track) -> None:
    """Write metadata tags so media servers can categorize tracks.
    
    Tags written: title, artist, albumartist, album, date, track number, genre.
    Requires ffmpeg to be available on the system.
    """
    if not file_path.exists():
        return

    # Keep the original media extension so ffmpeg can infer the output muxer.
    # Example: "song.mp3" -> "song.tagtmp.mp3" (not "song.mp3.tagtmp").
    tmp_path = file_path.with_name(f"{file_path.stem}.tagtmp{file_path.suffix}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(file_path),
        "-map",
        "0",
        "-c",
        "copy",
        "-metadata",
        f"title={track.title}",
        "-metadata",
        f"artist={track.artist}",
        "-metadata",
        f"albumartist={track.artist}",
    ]

    if track.album:
        cmd.extend(["-metadata", f"album={track.album}"])

    if track.year:
        cmd.extend(["-metadata", f"date={track.year}"])

    if track.track_number is not None:
        cmd.extend(["-metadata", f"track={track.track_number}"])

    if track.genre:
        cmd.extend(["-metadata", f"genre={track.genre}"])

    cmd.append(str(tmp_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[metadata] failed for {file_path.name}: {result.stderr.strip()}")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return

        tmp_path.replace(file_path)
    except FileNotFoundError:
        print("[metadata] ffmpeg not found; skipping metadata tagging")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[metadata] unexpected error for {file_path.name}: {exc}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def download_tracks(
    tracks: Iterable[Track],
    output_dir: Path,
    dry_run: bool,
    prevent_redownload_deleted: bool,
    audio_format: str,
) -> tuple[int, int]:
    yt_dlp_module = require_package("yt_dlp", "yt-dlp")
    YoutubeDL = yt_dlp_module.YoutubeDL

    output_dir.mkdir(parents=True, exist_ok=True)
    state_file = output_dir / STATE_FILE_NAME
    state = load_state(state_file)
    downloads_state: dict[str, str] = state.setdefault("downloads", {})

    downloaded = 0
    skipped_deleted = 0

    ydl_opts = {
        "quiet": False,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
        "ignoreerrors": True,
        "nooverwrites": True,
        "restrictfilenames": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "192",
            }
        ],
    }

    with YoutubeDL(ydl_opts) as ydl:
        for index, track in enumerate(tracks, start=1):
            previous_path_raw = downloads_state.get(track.key_str)
            previous_path = Path(previous_path_raw) if previous_path_raw else None

            if previous_path and previous_path.exists():
                print(f"[download {index}] {track.artist} - {track.title}")
                print("           already downloaded, skipping")
                continue

            if prevent_redownload_deleted and previous_path and not previous_path.exists():
                print(f"[download {index}] {track.artist} - {track.title}")
                print("           previously removed file detected, skipping")
                skipped_deleted += 1
                continue

            query = f"ytsearch1:{track.query} audio"
            print(f"[download {index}] {track.artist} - {track.title}")
            if dry_run:
                print(f"           dry-run query => {query}")
                downloaded += 1
                continue

            result = ydl.extract_info(query, download=True)
            if result:
                downloaded += 1
                resolved = result
                if isinstance(result, dict) and result.get("entries"):
                    entries = result.get("entries") or []
                    if entries:
                        resolved = entries[0]

                if isinstance(resolved, dict):
                    try:
                        # Use track info for filename instead of YouTube video metadata
                        # This prevents metadata mismatch when yt-dlp downloads a different song
                        safe_artist = re.sub(r'[<>:"/\\|?*]', '_', track.artist)
                        safe_title = re.sub(r'[<>:"/\\|?*]', '_', track.title)
                        filename = f"{safe_artist} - {safe_title}.{audio_format}"
                        final_path = (output_dir / filename).resolve()
                        
                        # Move the downloaded file to our desired location
                        downloaded_id = resolved.get('id', '')
                        if downloaded_id:
                            # Find the file that was just downloaded
                            for f in output_dir.glob(f"*[{downloaded_id}]*"):
                                if f.suffix == f".{audio_format}" or f.suffix in ['.mp3', '.m4a', '.ogg', '.opus', '.flac']:
                                    f.rename(final_path)
                                    break
                        
                        # Enrich track metadata from YouTube result and MusicBrainz
                        yt_meta = extract_youtube_metadata(resolved)
                        enriched_track = enrich_track_metadata(track, youtube_metadata=yt_meta)
                        
                        if enriched_track != track:
                            meta_parts = []
                            if enriched_track.album and not track.album:
                                meta_parts.append(f"album={enriched_track.album}")
                            if enriched_track.year and not track.year:
                                meta_parts.append(f"year={enriched_track.year}")
                            if enriched_track.genre and not track.genre:
                                meta_parts.append(f"genre={enriched_track.genre}")
                            if enriched_track.track_number and not track.track_number:
                                meta_parts.append(f"track={enriched_track.track_number}")
                            if meta_parts:
                                print(f"           enriched metadata: {', '.join(meta_parts)}")
                        
                        apply_metadata_tags(final_path, enriched_track)
                        downloads_state[track.key_str] = str(final_path)
                    except Exception as e:
                        print(f"[download] error processing file: {e}")

    if not dry_run:
        save_state(state_file, state)

    return downloaded, skipped_deleted


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download tracks from your latest Last.fm scrobbles, optionally filtering by liked songs from Spotify/YouTube Music."
    )
    parser.add_argument("--lastfm-user", required=True, help="Last.fm username")
    parser.add_argument(
        "--lastfm-api-key",
        default=os.getenv("LASTFM_API_KEY"),
        help="Last.fm API key (or set LASTFM_API_KEY env var)",
    )
    parser.add_argument("--limit", type=int, default=50, help="How many recent scrobbles to inspect")
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not remove duplicate artist/title combinations from recent scrobbles",
    )
    parser.add_argument(
        "--liked-only",
        action="store_true",
        help="Only download tracks present in liked songs from selected providers",
    )
    parser.add_argument(
        "--providers",
        default="spotify,ytmusic",
        help="Comma-separated liked providers to use when --liked-only is set (spotify,ytmusic)",
    )
    parser.add_argument(
        "--ytmusic-auth",
        default="headers_auth.json",
        help="Path to ytmusicapi auth headers file (used when provider includes ytmusic)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where downloaded files will be written (or set SCROBLOAD_OUTPUT_DIR)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without actually downloading",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously in the background, polling for new scrobbles",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=900,
        help="Seconds between daemon polling cycles (default: 900)",
    )
    parser.add_argument(
        "--redownload-deleted",
        action="store_true",
        help="Disable delete protection and allow tracks you manually removed to be downloaded again",
    )
    parser.add_argument(
        "--audio-format",
        default="mp3",
        help="Audio file extension/codec for downloads (default: mp3), e.g. mp3, ogg, opus, m4a, flac",
    )
    parser.add_argument(
        "--no-musicbrainz",
        action="store_true",
        help="Disable MusicBrainz metadata enrichment (faster downloads, less metadata)",
    )
    args = parser.parse_args(argv)

    if not args.lastfm_api_key:
        parser.error("Missing Last.fm API key. Provide --lastfm-api-key or LASTFM_API_KEY env var.")

    args.audio_format = args.audio_format.lower().strip().lstrip(".")
    if not args.audio_format or not re.fullmatch(r"[a-z0-9]+", args.audio_format):
        parser.error("Invalid --audio-format. Use letters/numbers only, e.g. mp3, ogg, opus, m4a.")

    return args


def run_once(args: argparse.Namespace) -> dict[str, int | bool]:
    providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    if args.liked_only and not providers:
        raise RuntimeError("--liked-only requires at least one provider in --providers")

    print("[lastfm] fetching recent scrobbles...")
    scrobbles = fetch_recent_scrobbles(
        user=args.lastfm_user,
        api_key=args.lastfm_api_key,
        limit=args.limit,
        unique=not args.no_dedupe,
    )
    print(f"[lastfm] got {len(scrobbles)} tracks")

    selected_tracks = scrobbles

    if args.liked_only:
        print("[likes] building liked-song index...")
        liked = build_liked_index(providers, args.ytmusic_auth)
        before = len(selected_tracks)
        selected_tracks = [track for track in selected_tracks if track.key in liked]
        print(f"[likes] filtered {before} -> {len(selected_tracks)} tracks")

    if not selected_tracks:
        print("No tracks to download after filtering.")
        return {
            "scrobbles_seen": len(scrobbles),
            "tracks_selected": 0,
            "download_attempted": 0,
            "liked_only": bool(args.liked_only),
        }

    downloaded_count, skipped_deleted = download_tracks(
        tracks=selected_tracks,
        output_dir=Path(args.output_dir),
        dry_run=args.dry_run,
        prevent_redownload_deleted=not args.redownload_deleted,
        audio_format=args.audio_format,
    )

    summary: dict[str, int | bool] = {
        "scrobbles_seen": len(scrobbles),
        "tracks_selected": len(selected_tracks),
        "download_attempted": downloaded_count,
        "skipped_deleted": skipped_deleted,
        "liked_only": bool(args.liked_only),
    }
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    return summary


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)

    if not args.daemon:
        run_once(args)
        return 0

    print(f"[daemon] started (poll interval: {args.poll_interval}s)")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[daemon] cycle {cycle}")
        try:
            run_once(args)
        except Exception as exc:
            print(f"[daemon] cycle error: {exc}")

        print(f"[daemon] sleeping {args.poll_interval}s")
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Interrupted.")
        raise SystemExit(130)
