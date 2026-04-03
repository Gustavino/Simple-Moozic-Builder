"""
Audio downloader for the Telegram bot pipeline.

Supports:
  - YouTube single track
  - YouTube playlist
  - Spotify single track  (via spotdl)
  - Spotify playlist      (via spotdl)

Always returns list[DownloadResult] — single tracks return a list of one item.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Use binaries from the same venv as this Python process.
# This ensures the correct binary is found when running under systemd,
# where PATH does not include the venv's bin directory.
_VENV_BIN = Path(sys.executable).parent
_SPOTDL = str(_VENV_BIN / "spotdl")
_YTDLP = str(_VENV_BIN / "yt-dlp")

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    artist: str
    thumbnail_path: Optional[Path]


# --------------------------------------------------------------------------- #
# URL detection
# --------------------------------------------------------------------------- #

def _is_spotify_track(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/track/", url))


def _is_spotify_playlist(url: str) -> bool:
    return bool(re.search(r"open\.spotify\.com/playlist/", url))


def _is_youtube_single(url: str) -> bool:
    return bool(re.search(r"(youtube\.com/watch|youtu\.be/)", url))


def _is_youtube_playlist(url: str) -> bool:
    return bool(re.search(r"youtube\.com/playlist\?", url))


def is_supported_url(url: str) -> bool:
    return any([
        _is_spotify_track(url),
        _is_spotify_playlist(url),
        _is_youtube_single(url),
        _is_youtube_playlist(url),
    ])


def is_playlist_url(url: str) -> bool:
    return _is_spotify_playlist(url) or _is_youtube_playlist(url)


# --------------------------------------------------------------------------- #
# Subprocess streaming helper
# --------------------------------------------------------------------------- #

def _run_streaming(cmd: list[str], timeout: int, label: str) -> None:
    """Runs a command and logs each output line in real time.

    stdout and stderr are merged so all progress messages appear in order.
    Raises RuntimeError if the process exits with a non-zero code.
    """
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        lines: list[str] = []
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                if line:
                    logger.info("[%s] %s", label, line)
                    lines.append(line)
        except Exception:
            pass

        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            tail = "\n".join(lines[-20:])
            raise RuntimeError(f"{label} falhou (exit {proc.returncode}):\n{tail}")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _collect_mp3s(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.mp3"), key=lambda p: p.stat().st_mtime)


def _collect_pngs(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.png"), key=lambda p: p.stat().st_mtime)


def _results_from_dir(directory: Path) -> list[DownloadResult]:
    """Builds DownloadResult entries for every MP3 found in directory."""
    results = []
    for mp3 in _collect_mp3s(directory):
        pngs = [p for p in _collect_pngs(directory) if p.stem.startswith(mp3.stem)]
        thumbnail = pngs[0] if pngs else None
        results.append(DownloadResult(
            audio_path=mp3,
            title=mp3.stem,
            artist="Unknown",
            thumbnail_path=thumbnail,
        ))
    return results


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

def _download_youtube(
    url: str, output_dir: Path, playlist: bool = False, config: dict | None = None,
) -> list[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use an isolated subdirectory so concurrent calls don't mix files
    batch_dir = output_dir / f"_dl_{uuid.uuid4().hex[:8]}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(batch_dir / "%(title)s.%(ext)s")
    playlist_flag = "--yes-playlist" if playlist else "--no-playlist"

    cmd = [
        _YTDLP,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--write-thumbnail",
        "--convert-thumbnails", "png",
        playlist_flag,
        "-o", output_template,
    ]

    # Use cookies to bypass YouTube bot detection / signature challenges
    cookies_file = (config or {}).get("youtube_cookies_file", "")
    if cookies_file and Path(cookies_file).is_file():
        cmd += ["--cookies", cookies_file]

    # Prefer the web client which supports cookies and avoids signature issues
    cmd += ["--extractor-args", "youtube:player_client=web"]

    cmd += ["--", url]

    _run_streaming(cmd, timeout=600, label="yt-dlp")

    results = _results_from_dir(batch_dir)
    if not results:
        raise FileNotFoundError(f"Nenhum MP3 encontrado em {batch_dir} apos download.")

    # Move files up to output_dir to keep a flat structure
    for r in results:
        dest = output_dir / r.audio_path.name
        r.audio_path.rename(dest)
        r.audio_path = dest
        if r.thumbnail_path:
            tdest = output_dir / r.thumbnail_path.name
            r.thumbnail_path.rename(tdest)
            r.thumbnail_path = tdest

    try:
        batch_dir.rmdir()
    except OSError:
        pass

    return results


# --------------------------------------------------------------------------- #
# Spotify (spotdl)
# --------------------------------------------------------------------------- #

def _download_spotify(url: str, output_dir: Path, config: dict | None = None) -> list[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)

    batch_dir = output_dir / f"_dl_{uuid.uuid4().hex[:8]}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        _SPOTDL,
        "download",
        url,
        "--output", str(batch_dir / "{title}"),
        "--format", "mp3",
        "--bitrate", "192k",
        "--threads", "4",
    ]

    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        cmd += ["--client-id", client_id, "--client-secret", client_secret]

    # spotdl uses yt-dlp internally; pass cookies to avoid YouTube bot detection
    cookies_file = (config or {}).get("youtube_cookies_file", "")
    if cookies_file and Path(cookies_file).is_file():
        cmd += ["--cookie-file", cookies_file]

    _run_streaming(cmd, timeout=600, label="spotdl")

    results = _results_from_dir(batch_dir)
    if not results:
        raise FileNotFoundError(f"Nenhum MP3 encontrado em {batch_dir} apos download.")

    for r in results:
        dest = output_dir / r.audio_path.name
        r.audio_path.rename(dest)
        r.audio_path = dest

    try:
        batch_dir.rmdir()
    except OSError:
        pass

    return results


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def download_tracks(url: str, output_dir: Path, config: dict | None = None) -> list[DownloadResult]:
    """Downloads one or more tracks from a YouTube or Spotify URL.

    Returns a list of DownloadResult — single tracks return a list of one.
    Raises ValueError for unsupported URLs, RuntimeError on download failure.
    """
    url = url.strip()
    if _is_spotify_track(url) or _is_spotify_playlist(url):
        return _download_spotify(url, output_dir, config=config)
    if _is_youtube_single(url):
        return _download_youtube(url, output_dir, playlist=False, config=config)
    if _is_youtube_playlist(url):
        return _download_youtube(url, output_dir, playlist=True, config=config)
    raise ValueError(
        f"Link nao suportado. Envie YouTube (track ou playlist) "
        f"ou Spotify (track ou playlist).\nRecebido: {url}"
    )
