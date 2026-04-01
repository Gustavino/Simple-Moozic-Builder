"""
Full pipeline: Telegram URL -> download -> convert -> build mod -> upload to Workshop.

Each step yields a status string so the bot can update the user in real time.
All SMB functions are called directly (no subprocess) since this module runs
in the same Python process as Simple Moozic Builder.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Generator

# Ensure the repo root is on sys.path so we can import SMB directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from simple_moozic_builder import (  # type: ignore
    build_mod_from_config,
    convert_single_audio_file,
    default_assets_root,
)
from bot.downloader import download_track, is_supported_url
from bot.steam_uploader import upload_mod


def run_pipeline(url: str, config: dict) -> Generator[str, None, None]:
    """Runs the full add-music pipeline and yields human-readable status messages.

    Args:
        url: YouTube or Spotify track URL.
        config: Bot configuration dict (from bot_config.json).

    Yields:
        Status strings to send back to the Telegram user.
    """
    if not is_supported_url(url):
        yield "Link nao suportado. Envie um link do YouTube ou Spotify."
        return

    work_dir = Path(config["work_dir"]).resolve()
    download_dir = work_dir / "downloads"
    audio_dir = work_dir / "_ogg"
    output_dir = work_dir / "OUTPUT"

    # ------------------------------------------------------------------ #
    # Step 1 — Download
    # ------------------------------------------------------------------ #
    yield "Baixando musica..."
    try:
        result = download_track(url, download_dir)
    except Exception as exc:
        yield f"Erro no download: {exc}"
        return

    yield f"Download concluido: {result.title} - {result.artist}"

    # ------------------------------------------------------------------ #
    # Step 2 — Convert to .ogg via SMB
    # ------------------------------------------------------------------ #
    yield "Convertendo para .ogg..."
    try:
        entry = convert_single_audio_file(result.audio_path, audio_dir, force=False)
    except SystemExit as exc:
        yield f"Erro na conversao: {exc}"
        return

    if not entry.ogg.exists():
        yield f"Erro: arquivo .ogg nao encontrado apos conversao ({entry.ogg})"
        return

    yield f"Conversao concluida: {entry.ogg.name}"

    # ------------------------------------------------------------------ #
    # Step 3 — Build the mod with SMB
    # ------------------------------------------------------------------ #
    yield "Construindo mod..."
    assets_root = config.get("assets_root") or str(default_assets_root())

    build_config: dict = {
        "mode": config.get("media_type", "cassette"),
        "mod_id": config["mod_id"],
        "name": config.get("mod_name", config["mod_id"]),
        "author": config.get("author", "local-builder"),
        "audio_dir": str(audio_dir),
        "out_dir": str(output_dir),
        "assets_root": assets_root,
        "parent_mod_id": config.get("parent_mod_id", "TrueMoozic"),
        "standalone_bundle": config.get("standalone_bundle", False),
    }

    workshop_cover = config.get("workshop_cover")
    if workshop_cover:
        build_config["workshop_cover"] = workshop_cover
    elif result.thumbnail_path and result.thumbnail_path.exists():
        build_config["workshop_cover"] = str(result.thumbnail_path)

    try:
        mod_output_path = build_mod_from_config(build_config)
    except SystemExit as exc:
        yield f"Erro no build do mod: {exc}"
        return

    yield f"Mod construido em: {mod_output_path}"

    # ------------------------------------------------------------------ #
    # Step 4 — Upload to Steam Workshop (optional)
    # ------------------------------------------------------------------ #
    workshop_item_id = config.get("workshop_item_id", "").strip()
    if not workshop_item_id:
        yield (
            f"Pronto! '{result.title}' adicionado ao mod.\n"
            "Upload para Workshop ignorado (workshop_item_id nao configurado)."
        )
        return

    yield "Enviando para Steam Workshop..."
    try:
        upload_mod(
            mod_output_dir=mod_output_path,
            workshop_item_id=workshop_item_id,
            steamcmd_path=config.get("steamcmd_path", "steamcmd"),
            steam_username=config.get("steam_username", ""),
            change_note=f"Added: {result.title} - {result.artist}",
        )
    except Exception as exc:
        yield f"Erro no upload para Workshop: {exc}"
        return

    yield (
        f"Pronto! '{result.title}' adicionado ao mod e publicado na Workshop.\n"
        "Os jogadores vao ver a musica apos atualizar o mod no jogo."
    )
