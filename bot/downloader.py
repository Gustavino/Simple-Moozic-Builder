"""
Audio downloader for the Telegram bot pipeline.

Supports YouTube and Spotify URLs via yt-dlp (YouTube) and spotdl (Spotify).
Downloaded files are placed in the given output_dir as MP3 for further
conversion by Simple Moozic Builder's audio pipeline.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    artist: str
    thumbnail_path: Optional[Path]


def _is_spotify_url(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/track/", url))


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com/watch|youtu\.be/)", url))


def is_supported_url(url: str) -> bool:
    return _is_spotify_url(url) or _is_youtube_url(url)


def _latest_file(directory: Path, pattern: str) -> Optional[Path]:
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _download_youtube(url: str, output_dir: Path) -> DownloadResult:
    """Downloads audio from YouTube using yt-dlp."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--write-thumbnail",
        "--convert-thumbnails", "png",
        "--no-playlist",
        "--print", "%(title)s\n%(artist)s",
        "-o", output_template,
        "--", url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed:\n{result.stderr.strip()}")

    lines = [l for l in result.stdout.strip().splitlines() if l]
    title = lines[0] if len(lines) > 0 else "Unknown"
    artist = lines[1] if len(lines) > 1 else "Unknown"
    # yt-dlp prints "NA" when the field is absent
    if artist in ("NA", ""):
        artist = "Unknown"

    audio_path = _latest_file(output_dir, "*.mp3")
    if audio_path is None:
        raise FileNotFoundError(f"No MP3 found in {output_dir} after yt-dlp download.")

    thumbnail_path = _latest_file(output_dir, "*.png")
    return DownloadResult(
        audio_path=audio_path,
        title=title,
        artist=artist,
        thumbnail_path=thumbnail_path,
    )


def _download_spotify(url: str, output_dir: Path) -> DownloadResult:
    """Downloads audio from a Spotify track URL using spotdl.

    spotdl matches the Spotify track metadata to a YouTube source and downloads it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "spotdl",
        "download",
        url,
        "--output", str(output_dir / "{title}"),
        "--format", "mp3",
        "--bitrate", "192k",
        "--threads", "1",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"spotdl download failed:\n{result.stderr.strip()}")

    audio_path = _latest_file(output_dir, "*.mp3")
    if audio_path is None:
        raise FileNotFoundError(f"No MP3 found in {output_dir} after spotdl download.")

    # Parse title and artist from the filename (spotdl uses "{title}" template)
    stem = audio_path.stem
    title = stem
    artist = "Unknown"

    return DownloadResult(
        audio_path=audio_path,
        title=title,
        artist=artist,
        thumbnail_path=None,
    )


def download_track(url: str, output_dir: Path) -> DownloadResult:
    """Entry point: detects URL type and routes to the correct downloader."""
    url = url.strip()
    if _is_spotify_url(url):
        return _download_spotify(url, output_dir)
    if _is_youtube_url(url):
        return _download_youtube(url, output_dir)
    raise ValueError(f"Unsupported URL. Send a YouTube or Spotify track link.\nReceived: {url}")
