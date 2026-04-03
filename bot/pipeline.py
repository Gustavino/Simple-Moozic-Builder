"""
Full pipeline: list of URLs -> download all -> convert all -> build mod once -> upload.

Designed for batch processing: a session collects N links (including playlists),
then a single build+upload covers all of them.

Dedup: tracks whose .ogg already exists in the cache are skipped before conversion.
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
from bot.downloader import download_tracks, is_supported_url, is_playlist_url


def _ogg_already_exists(audio_path: Path, ogg_cache_dir: Path) -> bool:
    """Returns True if a converted .ogg for this source already exists in the cache."""
    return (ogg_cache_dir / f"{audio_path.stem}.ogg").exists()


def _build_stamp_path(ogg_cache_dir: Path) -> Path:
    """Path to a file that records which .ogg stems were in the last successful build."""
    return ogg_cache_dir / ".last_build_oggs"


def _mod_needs_rebuild(ogg_cache_dir: Path) -> bool:
    """Returns True if the .ogg cache has tracks not included in the last successful build."""
    stamp = _build_stamp_path(ogg_cache_dir)
    if not stamp.is_file():
        # No successful build recorded — rebuild if there are any .ogg files
        return bool(list(ogg_cache_dir.glob("*.ogg")))
    built_stems = set(stamp.read_text().splitlines())
    cached_stems = {p.stem for p in ogg_cache_dir.glob("*.ogg")}
    return not cached_stems.issubset(built_stems)


def _write_build_stamp(ogg_cache_dir: Path) -> None:
    """Records the current .ogg cache contents after a successful build+upload."""
    stamp = _build_stamp_path(ogg_cache_dir)
    stems = sorted(p.stem for p in ogg_cache_dir.glob("*.ogg"))
    stamp.write_text("\n".join(stems))


def run_batch_pipeline(urls: list[str], config: dict) -> Generator[str, None, None]:
    """Processes a batch of URLs (tracks and/or playlists) in a single build+upload cycle.

    For each URL:
      - Expands playlists into individual tracks
      - Skips tracks already in the .ogg cache (dedup)
      - Downloads and converts new tracks

    Then runs one mod build and one Workshop upload for the whole batch.

    Yields:
        Human-readable status strings for the Telegram status message.
    """
    valid_urls = [u for u in urls if is_supported_url(u)]
    if not valid_urls:
        yield "Nenhum link valido na sessao."
        return

    work_dir = Path(config["work_dir"]).resolve()
    download_dir = work_dir / "downloads"
    ogg_cache_dir = work_dir / "_ogg"   # audio_cache_root returns this dir directly
    output_dir = work_dir / "OUTPUT"

    ogg_cache_dir.mkdir(parents=True, exist_ok=True)

    converted: list[tuple[str, str]] = []   # (title, artist) — newly added
    skipped: list[str] = []                 # titles already in mod
    last_thumbnail: Path | None = None

    # ------------------------------------------------------------------ #
    # Step 1 — Expand, dedup, download, convert
    # ------------------------------------------------------------------ #
    for url_idx, url in enumerate(valid_urls, start=1):
        prefix = f"[URL {url_idx}/{len(valid_urls)}]"
        label = "playlist" if is_playlist_url(url) else "track"
        yield f"{prefix} Baixando {label}..."

        try:
            results = download_tracks(url, download_dir, config=config)
        except Exception as exc:
            yield f"{prefix} Erro no download: {exc}"
            continue

        yield f"{prefix} {len(results)} faixa(s) encontrada(s). Processando..."

        for i, result in enumerate(results, start=1):
            track_prefix = f"{prefix} [{i}/{len(results)}]"

            # --- Dedup check ---
            if _ogg_already_exists(result.audio_path, ogg_cache_dir):
                skipped.append(result.title)
                yield f"{track_prefix} Ja existe no mod, pulando: {result.title}"
                continue

            yield f"{track_prefix} Convertendo: {result.title}"
            try:
                entry = convert_single_audio_file(result.audio_path, ogg_cache_dir, force=False)
            except SystemExit as exc:
                yield f"{track_prefix} Erro na conversao: {exc}"
                continue

            if not entry.ogg.exists():
                yield f"{track_prefix} Erro: .ogg nao encontrado apos conversao."
                continue

            converted.append((result.title, result.artist))
            if result.thumbnail_path and result.thumbnail_path.exists():
                last_thumbnail = result.thumbnail_path

    # ------------------------------------------------------------------ #
    # Summary before build
    # ------------------------------------------------------------------ #
    if skipped:
        yield f"Ja convertidas ({len(skipped)}, pulando download): {', '.join(skipped)}"

    needs_rebuild = _mod_needs_rebuild(ogg_cache_dir)

    if not converted and not needs_rebuild:
        yield "Todas as musicas ja estao no mod. Build cancelado."
        return

    if converted:
        new_summary = "\n".join(f"  • {t} - {a}" for t, a in converted)
        yield f"Novas musicas ({len(converted)}):\n{new_summary}"
    elif needs_rebuild:
        yield "Mod desatualizado — rebuild necessario."

    # ------------------------------------------------------------------ #
    # Step 2 — Build mod once with everything in ogg_cache_dir
    # ------------------------------------------------------------------ #
    total_oggs = len(list(ogg_cache_dir.glob("*.ogg")))
    yield f"Construindo mod ({total_oggs} faixa(s) total no mod)..."

    assets_root = config.get("assets_root") or str(default_assets_root())
    build_config: dict = {
        "mode": config.get("media_type", "cassette"),
        "mod_id": config["mod_id"],
        "name": config.get("mod_name", config["mod_id"]),
        "author": config.get("author", "local-builder"),
        "audio_dir": str(ogg_cache_dir),
        "out_dir": str(output_dir),
        "assets_root": assets_root,
        "parent_mod_id": config.get("parent_mod_id", "TrueMoozic"),
        "standalone_bundle": config.get("standalone_bundle", False),
        "custom_vinyls": config.get("custom_vinyls", False),
        "custom_cassettes": config.get("custom_cassettes", False),
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
        _write_build_stamp(ogg_cache_dir)
        yield (
            f"Mod buildado em: {mod_output_path}\n"
            "Workshop upload ignorado (workshop_item_id nao configurado)."
        )
        return

    from bot.steam_uploader import upload_mod

    track_names = ", ".join(t for t, _ in converted) if converted else "rebuild"
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

    _write_build_stamp(ogg_cache_dir)

    if converted:
        new_summary = "\n".join(f"  • {t} - {a}" for t, a in converted)
        yield (
            f"Pronto! {len(converted)} musica(s) nova(s) adicionada(s):\n{new_summary}\n\n"
            f"Mod agora tem {total_oggs} faixa(s) no total.\n"
            "Jogadores veem as musicas apos atualizar o mod no jogo."
        )
    else:
        yield (
            f"Rebuild concluido! Mod tem {total_oggs} faixa(s) no total.\n"
            "Jogadores veem as musicas apos atualizar o mod no jogo."
        )
