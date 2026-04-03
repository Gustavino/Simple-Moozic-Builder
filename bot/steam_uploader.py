"""
Steam Workshop uploader for Project Zomboid mods.

Uses steamcmd to update an existing Workshop item.
steamcmd must be installed and the account credentials must already be
cached (run `steamcmd +login <username> <password> +quit` once manually).

Project Zomboid App ID: 108600
"""

from __future__ import annotations

import subprocess
from pathlib import Path


PZ_APP_ID = "108600"


def _build_vdf(
    content_folder: Path, item_id: str, preview_image: Path,
    change_note: str, description: str = "",
) -> str:
    lines = [
        '"workshopitem"',
        "{",
        f'\t"appid"\t\t"{PZ_APP_ID}"',
        f'\t"publishedfileid"\t"{item_id}"',
        f'\t"contentfolder"\t"{content_folder}"',
        f'\t"previewfile"\t"{preview_image}"',
        f'\t"changenote"\t"{change_note}"',
    ]
    if description:
        escaped = description.replace('"', '\\"')
        lines.append(f'\t"description"\t"{escaped}"')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def upload_mod(
    mod_output_dir: Path,
    workshop_item_id: str,
    steamcmd_path: str = "steamcmd",
    steam_username: str = "",
    change_note: str = "New tracks added via Telegram bot",
    description: str = "",
) -> None:
    """Updates a Steam Workshop item with the built mod folder.

    Args:
        mod_output_dir: Path to the built mod root (contains mod.info).
        workshop_item_id: Numeric Workshop item ID (publishedfileid).
        steamcmd_path: Path or name of the steamcmd binary.
        steam_username: Steam account username (credentials must be cached).
        change_note: Short description of this update.
        description: Full Workshop description (song list, etc.).
    """
    preview_image = mod_output_dir / "poster.png"
    if not preview_image.exists():
        # Fallback: use any PNG in the mod root
        pngs = list(mod_output_dir.glob("*.png"))
        preview_image = pngs[0] if pngs else mod_output_dir / "poster.png"

    vdf_content = _build_vdf(mod_output_dir, workshop_item_id, preview_image, change_note, description)
    vdf_path = mod_output_dir / "workshop_upload.vdf"
    vdf_path.write_text(vdf_content, encoding="utf-8")

    login_args = ["+login", steam_username] if steam_username else ["+login", "anonymous"]

    cmd = [
        steamcmd_path,
        *login_args,
        "+workshop_build_item", str(vdf_path),
        "+quit",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"steamcmd workshop upload failed (exit {result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
