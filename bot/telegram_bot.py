"""
Telegram bot entry point for the Simple Moozic Builder pipeline.

Usage:
    python -m bot.telegram_bot --config bot_config.json

Authentication:
    Any user can start the bot, but must send the correct BOT_PASSWORD
    (from .env) to unlock music submission for their current session.
    Unlocked sessions persist in memory until the bot restarts.

Required .env keys: TELEGRAM_TOKEN, BOT_PASSWORD
Required config keys: mod_id, work_dir
Optional config keys: workshop_item_id, steamcmd_path, steam_username, media_type
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.downloader import is_supported_url
from bot.pipeline import run_pipeline

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load .env from repo root (parent of bot/)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #

def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # Secrets come from .env, not from the JSON file
    cfg["telegram_token"] = os.environ["TELEGRAM_TOKEN"]
    cfg["bot_password"] = os.environ["BOT_PASSWORD"]
    cfg.setdefault("steam_username", os.environ.get("STEAM_USERNAME", ""))
    return cfg


# --------------------------------------------------------------------------- #
# Session auth (in-memory, resets on bot restart)
# --------------------------------------------------------------------------- #

def _is_unlocked(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    unlocked: set = context.bot_data.setdefault("unlocked_users", set())
    return user_id in unlocked


def _unlock(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data.setdefault("unlocked_users", set()).add(user_id)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_unlocked(update.effective_user.id, context):
        await update.message.reply_text(
            "Ja autenticado! Envie um link do YouTube ou Spotify."
        )
    else:
        await update.message.reply_text(
            "Bem-vindo ao Moozic Bot!\n\n"
            "Envie a senha para desbloquear o envio de musicas."
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_unlocked(update.effective_user.id, context):
        await update.message.reply_text("Envie a senha primeiro.")
        return

    config: dict = context.bot_data["config"]
    workshop_id = config.get("workshop_item_id") or "nao configurado"
    media_type = config.get("media_type", "cassette")
    mod_id = config.get("mod_id", "?")

    await update.message.reply_text(
        f"Status do bot:\n"
        f"  Mod ID: {mod_id}\n"
        f"  Tipo de midia: {media_type}\n"
        f"  Workshop item: {workshop_id}\n"
        f"  Sessao: desbloqueada"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: dict = context.bot_data["config"]
    user = update.effective_user
    text = (update.message.text or "").strip()

    # --- Password check ---
    if not _is_unlocked(user.id, context):
        if text == config["bot_password"]:
            _unlock(user.id, context)
            await update.message.reply_text(
                "Senha correta! Pode enviar links do YouTube ou Spotify."
            )
            logger.info("User %s (%s) authenticated.", user.id, user.username)
        else:
            await update.message.reply_text("Senha incorreta.")
        return

    # --- URL handling ---
    if not is_supported_url(text):
        await update.message.reply_text(
            "Envie um link valido do YouTube ou Spotify.\n"
            "Exemplos:\n"
            "  https://www.youtube.com/watch?v=...\n"
            "  https://open.spotify.com/track/..."
        )
        return

    status_msg = await update.message.reply_text("Iniciando pipeline...")

    def pipeline_sync() -> None:
        loop = asyncio.get_event_loop()
        for status_text in run_pipeline(text, config):
            logger.info("[pipeline] %s", status_text)
            future = asyncio.run_coroutine_threadsafe(
                status_msg.edit_text(status_text), loop
            )
            try:
                future.result(timeout=10)
            except Exception as edit_exc:
                logger.warning("Could not edit status message: %s", edit_exc)

    try:
        await asyncio.to_thread(pipeline_sync)
    except Exception as exc:
        logger.exception("Pipeline error for URL: %s", text)
        await status_msg.edit_text(f"Erro inesperado no pipeline:\n{exc}")


# --------------------------------------------------------------------------- #
# Bot setup
# --------------------------------------------------------------------------- #

def build_application(config: dict) -> Application:
    app = Application.builder().token(config["telegram_token"]).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Simple Moozic Builder - Telegram Bot")
    parser.add_argument(
        "--config",
        default="bot_config.json",
        help="Path to bot_config.json (default: bot_config.json)",
    )
    ns = parser.parse_args()

    config_path = Path(ns.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        print("Copy bot_config.json.example to bot_config.json and fill in the values.", file=sys.stderr)
        sys.exit(1)

    for env_key in ("TELEGRAM_TOKEN", "BOT_PASSWORD"):
        if not os.environ.get(env_key):
            print(f"Missing required environment variable: {env_key}", file=sys.stderr)
            print("Copy .env.example to .env and fill in the values.", file=sys.stderr)
            sys.exit(1)

    config = load_config(config_path)

    required_cfg = ["mod_id", "work_dir"]
    missing = [k for k in required_cfg if not config.get(k)]
    if missing:
        print(f"Missing required bot_config.json keys: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    Path(config["work_dir"]).mkdir(parents=True, exist_ok=True)

    logger.info("Starting Moozic Bot for mod '%s'", config["mod_id"])
    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
