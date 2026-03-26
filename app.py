#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import requests


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_OUTPUT_DIR = os.getenv("SCROBLOAD_OUTPUT_DIR", "downloads")


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

    @property
    def key(self) -> tuple[str, str]:
        return (normalize_text(self.artist), normalize_text(self.title))

    @property
    def query(self) -> str:
        return f"{self.artist} - {self.title}"


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


def download_tracks(tracks: Iterable[Track], output_dir: Path, dry_run: bool) -> int:
    yt_dlp_module = require_package("yt_dlp", "yt-dlp")
    YoutubeDL = yt_dlp_module.YoutubeDL

    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0

    ydl_opts = {
        "quiet": False,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
        "ignoreerrors": True,
        "restrictfilenames": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        for index, track in enumerate(tracks, start=1):
            query = f"ytsearch1:{track.query} audio"
            print(f"[download {index}] {track.artist} - {track.title}")
            if dry_run:
                print(f"           dry-run query => {query}")
                downloaded += 1
                continue

            result = ydl.extract_info(query, download=True)
            if result:
                downloaded += 1

    return downloaded


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

    args = parser.parse_args(argv)

    if not args.lastfm_api_key:
        parser.error("Missing Last.fm API key. Provide --lastfm-api-key or LASTFM_API_KEY env var.")

    return args


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)

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
        return 0

    downloaded_count = download_tracks(
        tracks=selected_tracks,
        output_dir=Path(args.output_dir),
        dry_run=args.dry_run,
    )

    summary = {
        "scrobbles_seen": len(scrobbles),
        "tracks_selected": len(selected_tracks),
        "download_attempted": downloaded_count,
        "liked_only": bool(args.liked_only),
    }
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Interrupted.")
        raise SystemExit(130)
