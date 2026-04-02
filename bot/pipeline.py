"""
Full pipeline: list of URLs -> download all -> convert all -> build mod once -> upload.

Designed for batch processing: a session collects N links, then a single
build+upload covers all of them, keeping Workshop updates minimal.

All SMB functions are called directly (no subprocess) since this module runs
in the same Python process as Simple Moozic Builder.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Generator

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


def run_batch_pipeline(urls: list[str], config: dict) -> Generator[str, None, None]:
    """Processes a batch of URLs in a single build+upload cycle.

    Downloads and converts each URL individually, then runs one mod build
    and one Workshop upload covering all tracks in the batch.

    Args:
        urls: List of YouTube or Spotify track URLs.
        config: Bot configuration dict (from bot_config.json + .env).

    Yields:
        Human-readable status strings for the Telegram status message.
    """
    valid_urls = [u for u in urls if is_supported_url(u)]
    if not valid_urls:
        yield "Nenhum link valido na sessao."
        return

    work_dir = Path(config["work_dir"]).resolve()
    download_dir = work_dir / "downloads"
    audio_dir = work_dir / "_ogg"
    output_dir = work_dir / "OUTPUT"

    total = len(valid_urls)
    downloaded: list[tuple[str, str]] = []   # (title, artist) for summary
    last_thumbnail: Path | None = None

    # ------------------------------------------------------------------ #
    # Step 1 — Download + convert each track
    # ------------------------------------------------------------------ #
    for i, url in enumerate(valid_urls, start=1):
        yield f"[{i}/{total}] Baixando..."
        try:
            result = download_track(url, download_dir)
        except Exception as exc:
            yield f"[{i}/{total}] Erro no download: {exc}"
            continue

        yield f"[{i}/{total}] Convertendo: {result.title}"
        try:
            entry = convert_single_audio_file(result.audio_path, audio_dir, force=False)
        except SystemExit as exc:
            yield f"[{i}/{total}] Erro na conversao: {exc}"
            continue

        if not entry.ogg.exists():
            yield f"[{i}/{total}] Erro: .ogg nao encontrado apos conversao."
            continue

        downloaded.append((result.title, result.artist))
        if result.thumbnail_path and result.thumbnail_path.exists():
            last_thumbnail = result.thumbnail_path

    if not downloaded:
        yield "Nenhuma musica convertida com sucesso. Build cancelado."
        return

    summary_lines = "\n".join(f"  • {t} - {a}" for t, a in downloaded)
    yield f"Download concluido ({len(downloaded)}/{total}):\n{summary_lines}"

    # ------------------------------------------------------------------ #
    # Step 2 — Build mod once with everything in audio_dir
    # ------------------------------------------------------------------ #
    yield f"Construindo mod com {len(downloaded)} musica(s)..."
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
    elif last_thumbnail:
        build_config["workshop_cover"] = str(last_thumbnail)

    try:
        if build_config["mode"] == "both":
            build_config["mode"] = "cassette"
            build_mod_from_config(build_config)
            build_config["mode"] = "vinyl"
            mod_output_path = build_mod_from_config(build_config)
        else:
            mod_output_path = build_mod_from_config(build_config)
    except SystemExit as exc:
        yield f"Erro no build do mod: {exc}"
        return

    # ------------------------------------------------------------------ #
    # Step 3 — Upload to Steam Workshop
    # ------------------------------------------------------------------ #
    workshop_item_id = config.get("workshop_item_id", "").strip()
    if not workshop_item_id:
        yield (
            f"Mod buildado em: {mod_output_path}\n"
            "Workshop upload ignorado (workshop_item_id nao configurado)."
        )
        return

    track_names = ", ".join(t for t, _ in downloaded)
    yield "Enviando para Steam Workshop..."
    try:
        upload_mod(
            mod_output_dir=mod_output_path,
            workshop_item_id=workshop_item_id,
            steamcmd_path=config.get("steamcmd_path", "steamcmd"),
            steam_username=config.get("steam_username", ""),
            change_note=f"Added: {track_names}",
        )
    except Exception as exc:
        yield f"Erro no upload para Workshop: {exc}"
        return

    yield (
        f"Pronto! {len(downloaded)} musica(s) adicionada(s) ao mod:\n"
        f"{summary_lines}\n\n"
        "Jogadores veem as musicas apos atualizar o mod no jogo."
    )
