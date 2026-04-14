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
    cover_art_url: str | None = None

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
        # Extract only first artist when multiple are present (split on common separators)
        artist = re.split(r'\s*(?:&|,| x | ft\.?| feat\.?| vs\.?| with )\s*', artist, flags=re.IGNORECASE)[0].strip()
        title = item.get("name", "").strip()
        album = item.get("album", {}).get("#text", "").strip()

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
    """Look up album, year, track number, genre, and cover art from MusicBrainz."""
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
            release_id = (release.get("id") or "").strip()
            album_name = release.get("title", "").strip()
            # Filter out placeholder album names for singles
            if album_name and album_name.lower() not in ["[unknown album]", "unknown album", "unknown"]:
                metadata["album"] = album_name

            if release_id:
                # Cover Art Archive fronts MusicBrainz release artwork.
                metadata["cover_art_url"] = f"https://coverartarchive.org/release/{release_id}/front-500"

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


def _lastfm_request(params: dict, timeout: int = 20) -> dict:
    response = requests.get(LASTFM_API_URL, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"Last.fm API error {payload['error']}: {payload.get('message', 'unknown error')}")
    return payload


def _extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(19\d{2}|20\d{2}|2100)\b", value)
    return match.group(1) if match else None


def _extract_lastfm_image(images: list[dict] | None) -> str | None:
    if not images:
        return None
    preferred_sizes = ("extralarge", "large", "medium", "small")
    ordered = sorted(
        images,
        key=lambda img: preferred_sizes.index(img.get("size")) if img.get("size") in preferred_sizes else 999,
    )
    for image in ordered:
        url = (image.get("#text") or "").strip()
        if url:
            return url
    return None


def fetch_metadata_from_lastfm(track: Track, api_key: str) -> dict:
    """Look up track metadata from Last.fm, with album.getInfo fallback for richer album data."""
    metadata: dict = {}
    try:
        track_payload = _lastfm_request(
            {
                "method": "track.getInfo",
                "artist": track.artist,
                "track": track.title,
                "autocorrect": 1,
                "api_key": api_key,
                "format": "json",
            }
        )
        track_info = track_payload.get("track", {}) if isinstance(track_payload, dict) else {}
        if not isinstance(track_info, dict):
            return metadata

        album_info = track_info.get("album")
        if isinstance(album_info, dict):
            album_title = (album_info.get("title") or "").strip()
            if album_title and album_title.lower() not in {"[unknown album]", "unknown album", "unknown"}:
                metadata["album"] = album_title
            image_url = _extract_lastfm_image(album_info.get("image"))
            if image_url:
                metadata["cover_art_url"] = image_url

            track_rank = str(album_info.get("@attr", {}).get("position", "")).strip()
            if track_rank.isdigit():
                metadata["track_number"] = int(track_rank)

        toptags = track_info.get("toptags", {}).get("tag", [])
        if isinstance(toptags, dict):
            toptags = [toptags]
        if isinstance(toptags, list):
            for tag in toptags:
                if isinstance(tag, dict):
                    genre = (tag.get("name") or "").strip()
                    if genre:
                        metadata["genre"] = genre
                        break

        if track_info.get("wiki"):
            year = _extract_year(track_info.get("wiki", {}).get("published") or track_info.get("wiki", {}).get("summary"))
            if year:
                metadata["year"] = year

        album_name_for_lookup = metadata.get("album") or track.album
        if album_name_for_lookup:
            try:
                album_payload = _lastfm_request(
                    {
                        "method": "album.getInfo",
                        "artist": track.artist,
                        "album": album_name_for_lookup,
                        "autocorrect": 1,
                        "api_key": api_key,
                        "format": "json",
                    }
                )
                album_obj = album_payload.get("album", {}) if isinstance(album_payload, dict) else {}
                if isinstance(album_obj, dict):
                    if not metadata.get("cover_art_url"):
                        album_image_url = _extract_lastfm_image(album_obj.get("image"))
                        if album_image_url:
                            metadata["cover_art_url"] = album_image_url

                    if not metadata.get("genre"):
                        album_tags = album_obj.get("tags", {}).get("tag", [])
                        if isinstance(album_tags, dict):
                            album_tags = [album_tags]
                        if isinstance(album_tags, list):
                            for tag in album_tags:
                                if isinstance(tag, dict):
                                    genre = (tag.get("name") or "").strip()
                                    if genre:
                                        metadata["genre"] = genre
                                        break

                    if not metadata.get("year"):
                        wiki = album_obj.get("wiki", {})
                        year = _extract_year(
                            (wiki.get("published") if isinstance(wiki, dict) else None)
                            or (wiki.get("summary") if isinstance(wiki, dict) else None)
                            or album_obj.get("releasedate")
                        )
                        if year:
                            metadata["year"] = year
            except Exception:
                pass
    except Exception:
        pass
    return metadata


def parse_youtube_title(youtube_title: str, expected_artist: str, expected_title: str | None = None) -> str | None:
    """Parse YouTube video title to extract track name.
    
    Handles titles with arbitrary prefixes like genres, tags, etc before the actual track.
    Supports formats:
      - "Artist - Track Name"
      - "Genre - Artist - Track Name"
      - "Tag - Another Tag - Artist - Track Name"
      - "Any Prefix - Track Name"
    
    If expected_title is provided, will try to find the matching title at the end after any number of separators.
    """
    if not youtube_title:
        return None
    
    expected_artist_lower = normalize_text(expected_artist)
    remaining = youtube_title.strip()
    
    # First try standard artist prefix stripping (handles multiple artist prefixes)
    while True:
        if " - " not in remaining:
            break
            
        parts = remaining.split(" - ", 1)
        if len(parts) != 2:
            break
            
        prefix_part = parts[0].strip()
        prefix_part_normalized = normalize_text(prefix_part)
        
        if expected_artist_lower == prefix_part_normalized:
            remaining = parts[1].strip()
        else:
            # If we have expected title, check if this prefix is NOT our title and skip it (genre/tag)
            if expected_title is not None:
                expected_title_normalized = normalize_text(expected_title)
                current_remaining_normalized = normalize_text(remaining)
                
                # If remaining already ends with our title, stop stripping
                if current_remaining_normalized.endswith(expected_title_normalized):
                    break
                
                # Otherwise allow stripping any prefix (genres, tags, etc.)
                remaining = parts[1].strip()
            else:
                break
    
    # Also check if the remaining still starts with artist name even without proper separator
    remaining_lower = remaining.lower()
    artist_prefix = expected_artist_lower + ' '
    if remaining_lower.startswith(artist_prefix):
        remaining = remaining[len(expected_artist):].strip()
        # Remove leading dash if present after stripping artist
        if remaining.startswith('-'):
            remaining = remaining[1:].strip()
    
    # If we have expected title, verify we actually got it
    if expected_title is not None:
        expected_normalized = normalize_text(expected_title)
        remaining_normalized = normalize_text(remaining)
        
        if remaining_normalized == expected_normalized:
            return remaining
    
    # Otherwise return if we removed something and result is not empty
    elif remaining != youtube_title.strip() and remaining:
        return remaining
    
    return None


def extract_youtube_metadata(ydl_result: dict, expected_artist: str | None = None) -> dict:
    """Extract useful metadata from a yt-dlp download result."""
    metadata: dict = {}

    if not isinstance(ydl_result, dict):
        return metadata

    # Try to extract album from YouTube video metadata
    album = ydl_result.get("album")
    if album:
        album_str = str(album).strip()
        # Filter out placeholder album names for singles
        if album_str.lower() not in ["[unknown album]", "unknown album", "unknown"]:
            metadata["album"] = album_str

    # Check if video contains multiple songs
    title = ydl_result.get("title", "")
    description = ydl_result.get("description", "")
    if title and description:
        # Check for common multi-song indicators in title
        multi_song_indicators = [
            "mix", "mashup", "medley", "compilation", "megamix", "playlist",
            "top songs", "best songs", "greatest hits", "full album",
            "album playlist", "song collection", "music mix"
        ]
        title_lower = title.lower()
        is_multi_song = any(indicator in title_lower for indicator in multi_song_indicators)

        # Additional checks for multi-song videos
        if not is_multi_song:

            # Check for multiple song separators - only if they are actually separating different tracks
            # Only flag as multi-song if there are 3 OR MORE " - " separators (allow 1 or 2 for standard Artist - Title format)
            if title.count(" - ") > 2 or title.count(":") > 2:
                is_multi_song = True

        # Check description for multi-song patterns
        if not is_multi_song and description:
            # Look for track listings or multiple song mentions
            track_listing_pattern = r'\d+\.\s*[^.\n]+'
            if re.search(track_listing_pattern, description, re.IGNORECASE):
                is_multi_song = True

            # Look for common multi-song phrases in description
            multi_song_desc_indicators = [
                "tracklist", "track list", "songs included"
            ]
            desc_lower = description.lower()
            if any(indicator in desc_lower for indicator in multi_song_desc_indicators):
                is_multi_song = True

        if is_multi_song:
            metadata["is_multi_song"] = True
            metadata["multi_song_reason"] = "Title/description suggests multiple songs"

    # Check for modified / sped up / slowed down versions
    title_lower = title.lower()
    modified_indicators = [
        "sped up", "speed up", "fast version", "slowed down", "slow down",
        "slow version", "reverb", "slowed + reverb", "sped + reverb",
        "nightcore", "daycore", "remix", "acoustic", "instrumental",
        "cover", "live", "live version", "8d audio", "bass boosted",
        "slowed & reverb", "sped & reverb", "edit", "version"
    ]
    
    is_modified_version = any(indicator in title_lower for indicator in modified_indicators)
    if is_modified_version:
        metadata["is_modified_version"] = True
        metadata["modified_reason"] = "Title indicates modified track version detected"
    # Extract year from upload date
    upload_date = ydl_result.get("upload_date", "")
    if upload_date and len(upload_date) >= 4:
        year = upload_date[:4]
        if year.isdigit() and 1900 <= int(year) <= 2100:
            metadata["year"] = year

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

        # Try to extract year from description if not found in upload_date
        if "year" not in metadata:
            year_match = re.search(r"(?:Year|Año|Released|Release Date)[:\s]+(\d{4})", description, re.IGNORECASE)
            if year_match:
                year = year_match.group(1)
                if 1900 <= int(year) <= 2100:
                    metadata["year"] = year

    # Extract from channel/uploader as album artist hint
    uploader = ydl_result.get("uploader", "")
    if uploader:
        metadata["youtube_uploader"] = uploader

    # Try to extract track number from video title (e.g., "Track 01 - Song Name")
    title = ydl_result.get("title", "")
    if title:
        track_match = re.search(r"(?:Track|Tr\.|№|#)\s*(\d+)", title, re.IGNORECASE)
        if track_match:
            track_num = track_match.group(1)
            if track_num.isdigit():
                metadata["track_number"] = int(track_num)
        
        # Try to extract track name from YouTube title if it follows "Artist - Track" format
        if expected_artist:
            parsed_track_name = parse_youtube_title(title, expected_artist)
            if parsed_track_name:
                metadata["youtube_track_name"] = parsed_track_name

    return metadata


def enrich_track_metadata(
    track: Track,
    lastfm_api_key: str,
    youtube_metadata: dict | None = None,
    use_musicbrainz: bool = True,
) -> Track:
    """Enrich a track with metadata from multiple sources.

    Priority: Track's existing data > Last.fm metadata > YouTube metadata > MusicBrainz metadata
    Only fills in missing fields - never overwrites existing data.
    """
    updates: dict = {}

    # Skip enrichment if video contains multiple songs
    if youtube_metadata and youtube_metadata.get("is_multi_song"):
        return track
    # Use Last.fm first for rich canonical music metadata.
    needs_lastfm = not all([track.album, track.year, track.track_number, track.genre, track.cover_art_url])
    if needs_lastfm:
        lastfm_meta = fetch_metadata_from_lastfm(track, lastfm_api_key)
        if lastfm_meta:
            if not track.album and lastfm_meta.get("album"):
                updates["album"] = lastfm_meta["album"]
            if not track.year and lastfm_meta.get("year"):
                updates["year"] = lastfm_meta["year"]
            if not track.track_number and lastfm_meta.get("track_number"):
                updates["track_number"] = lastfm_meta["track_number"]
            if not track.genre and lastfm_meta.get("genre"):
                updates["genre"] = lastfm_meta["genre"]
            if not track.cover_art_url and lastfm_meta.get("cover_art_url"):
                updates["cover_art_url"] = lastfm_meta["cover_art_url"]

    # Then use YouTube metadata as fallback (only for missing fields)
    if youtube_metadata:
        if not track.album and youtube_metadata.get("album"):
            updates["album"] = youtube_metadata["album"]
        if not track.genre and youtube_metadata.get("genre"):
            updates["genre"] = youtube_metadata["genre"]
        # If YouTube title had "Artist - Track" format, use the parsed track name
        # Always clean existing title if we found a cleaner version without duplicate artist
        if youtube_metadata.get("youtube_track_name"):
            # Only update if parsed title is different and valid
            parsed_title = youtube_metadata["youtube_track_name"].strip()
            if parsed_title and parsed_title != track.title:
                # Verify parsed title is actually shorter (we removed duplicate artist)
                if len(parsed_title) < len(track.title):
                    updates["title"] = parsed_title

    # Try MusicBrainz for missing metadata (only if enabled and we still have gaps)
    if use_musicbrainz:
        # Check what metadata we're still missing
        needs_album = not track.album and not updates.get("album")
        needs_year = not track.year and not updates.get("year")
        needs_track_number = not track.track_number and not updates.get("track_number")
        needs_genre = not track.genre and not updates.get("genre")
        needs_cover_art = not track.cover_art_url and not updates.get("cover_art_url")
        
        # Only query MusicBrainz if we have missing metadata
        if needs_album or needs_year or needs_track_number or needs_genre or needs_cover_art:
            try:
                mb_meta = fetch_metadata_from_musicbrainz(track.artist, track.title)
                if mb_meta:
                    # Only add missing fields from MusicBrainz
                    if needs_album and mb_meta.get("album"):
                        updates["album"] = mb_meta["album"]
                    if needs_year and mb_meta.get("year"):
                        updates["year"] = mb_meta["year"]
                    if needs_track_number and mb_meta.get("track_number"):
                        updates["track_number"] = mb_meta["track_number"]
                    if needs_genre and mb_meta.get("genre"):
                        updates["genre"] = mb_meta["genre"]
                    if needs_cover_art and mb_meta.get("cover_art_url"):
                        updates["cover_art_url"] = mb_meta["cover_art_url"]
            except Exception as e:
                # Silently continue if MusicBrainz fails - don't lose existing metadata
                pass

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
    cover_path: Path | None = None

    if track.cover_art_url:
        cover_candidate = file_path.with_name(f"{file_path.stem}.cover.jpg")
        try:
            image_resp = requests.get(track.cover_art_url, timeout=20)
            if image_resp.status_code == 200 and image_resp.content:
                cover_candidate.write_bytes(image_resp.content)
                cover_path = cover_candidate
        except Exception:
            cover_path = None

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(file_path),
    ]

    if cover_path:
        cmd.extend(["-i", str(cover_path), "-map", "0", "-map", "1", "-c", "copy"])
        cmd.extend(["-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
    else:
        cmd.extend(["-map", "0", "-c", "copy"])

    cmd.extend(["-metadata", f"title={track.title}", "-metadata", f"artist={track.artist}", "-metadata", f"albumartist={track.artist}"])

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
            if cover_path and cover_path.exists():
                cover_path.unlink(missing_ok=True)
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
    finally:
        if cover_path and cover_path.exists():
            cover_path.unlink(missing_ok=True)


def download_tracks(
    tracks: Iterable[Track],
    output_dir: Path,
    dry_run: bool,
    prevent_redownload_deleted: bool,
    audio_format: str,
    lastfm_api_key: str,
    use_musicbrainz: bool = True,
    no_metadata: bool = False,
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
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
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
                print("           already downloaded, refreshing metadata")
                if not no_metadata:
                    try:
                        enriched_existing = enrich_track_metadata(
                            track,
                            lastfm_api_key=lastfm_api_key,
                            youtube_metadata=None,
                            use_musicbrainz=use_musicbrainz,
                        )
                        apply_metadata_tags(previous_path, enriched_existing)
                    except Exception as e:
                        print(f"           metadata refresh failed: {e}")
                continue

            if prevent_redownload_deleted and previous_path and not previous_path.exists():
                print(f"[download {index}] {track.artist} - {track.title}")
                print("           previously removed file detected, skipping")
                skipped_deleted += 1
                continue

            # Search for first 5 results to find an original unmodified track
            # Removed "audio" suffix - this was excluding official music videos which are the highest quality sources
            query = f"ytsearch5:{track.query}"
            print(f"[download {index}] {track.artist} - {track.title}")
            if dry_run:
                print(f"           dry-run query => {query}")
                downloaded += 1
                continue

            result = ydl.extract_info(query, download=False)
            selected_entry = None
            
            if result and isinstance(result, dict) and result.get("entries"):
                entries = [e for e in result.get("entries") or [] if e]
                
                # Check each result until we find an original unmodified track
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    
                    yt_meta = extract_youtube_metadata(entry, expected_artist=track.artist)
                    
                    # Skip multi-song videos
                    if yt_meta.get("is_multi_song"):
                        print(f"           skipping result: multiple songs")
                        continue
                    
                    # Skip modified / sped up / slowed down versions
                    if yt_meta.get("is_modified_version"):
                        print(f"           skipping result: {yt_meta.get('modified_reason', 'modified version')}")
                        continue
                    
                    # Check if video title actually contains our track title (handle genre prefixes)
                    video_title_normalized = normalize_text(entry.get("title", ""))
                    track_title_normalized = normalize_text(track.title)
                    
                    # If the video title contains our actual track name, accept it even if artist prefix wasn't matching
                    # This handles cases like "Breakcore - I wish you stayed.." where genre comes first
                    if track_title_normalized in video_title_normalized:
                        selected_entry = entry
                        break
                    
                    # Also check parsed title if we got one
                    if yt_meta.get("youtube_track_name"):
                        parsed_normalized = normalize_text(yt_meta["youtube_track_name"])
                        if parsed_normalized == track_title_normalized:
                            selected_entry = entry
                            break
                    
                    # Fallback: allow if it's the first result and looks close enough
                    if entry == entries[0] and len(track_title_normalized) > 3:
                        selected_entry = entry
                        break
            
            if not selected_entry:
                print(f"           no valid original version found in search results, skipping")
                continue
            
            # Download only the selected valid track
            resolved = ydl.extract_info(selected_entry.get("webpage_url"), download=True)
            if resolved:
                downloaded += 1
                if isinstance(resolved, dict):
                    try:
                        yt_meta = extract_youtube_metadata(resolved, expected_artist=track.artist)

                        if no_metadata:
                            # Skip metadata enrichment completely
                            enriched_track = track
                        else:
                            # Enrich track metadata from YouTube result and MusicBrainz FIRST
                            enriched_track = enrich_track_metadata(
                                track,
                                lastfm_api_key=lastfm_api_key,
                                youtube_metadata=yt_meta,
                                use_musicbrainz=use_musicbrainz,
                            )

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
                                if enriched_track.cover_art_url and not track.cover_art_url:
                                    meta_parts.append("cover_art=lastfm")
                                if enriched_track.title != track.title:
                                    meta_parts.append(f"title={enriched_track.title}")
                                if meta_parts:
                                    print(f"           enriched metadata: {', '.join(meta_parts)}")

                        # Use the ENRICHED track info for filename, so we get the cleaned title
                        # This prevents metadata mismatch when yt-dlp downloads a different song
                        safe_artist = re.sub(r'[<>:"/\\|?*]', '_', enriched_track.artist)
                        safe_title = re.sub(r'[<>:"/\\|?*]', '_', enriched_track.title)
                        filename = f"{safe_artist} - {safe_title}.{audio_format}"
                        final_path = (output_dir / filename).resolve()

                        # Move the downloaded file to our desired location
                        downloaded_id = resolved.get('id', '')
                        if downloaded_id:
                            # Find the file that was just downloaded (now named as ID.ext)
                            temp_file = output_dir / f"{downloaded_id}.{audio_format}"
                            if temp_file.exists():
                                temp_file.rename(final_path)
                            else:
                                # Fallback: try to find any file with the ID
                                for f in output_dir.glob(f"{downloaded_id}.*"):
                                    if f.suffix in ['.mp3', '.m4a', '.ogg', '.opus', '.flac']:
                                        f.rename(final_path)
                                        break

                        if not no_metadata:
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
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip metadata tagging entirely (save raw downloaded files without any metadata)",
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
        lastfm_api_key=args.lastfm_api_key,
        use_musicbrainz=not args.no_musicbrainz,
        no_metadata=args.no_metadata,
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
