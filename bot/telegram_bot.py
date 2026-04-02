"""
Telegram bot with per-user debounce sessions and a global build queue.

Session flow:
  1. User sends a link → added to their pending buffer, 60s idle timer resets.
  2. After 60s without a new link → session closes, batch joins the global queue.
  3. Global queue ensures only one build+upload runs at a time.
  4. Users waiting in queue are notified of their position.

Auth flow:
  - User must type BOT_PASSWORD once to unlock their session.
  - Unlock persists in memory until bot restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from telegram import Message, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.downloader import is_supported_url, is_playlist_url
from bot.pipeline import run_batch_pipeline

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

IDLE_TIMEOUT_SECONDS = 60


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class UserSession:
    """Holds the pending URL buffer and debounce timer for one user."""
    urls: list[str] = field(default_factory=list)
    timer_task: asyncio.Task | None = None
    # The "X musicas na sessao" message to keep editing as links arrive
    status_message: Message | None = None


@dataclass
class BatchJob:
    """A closed session ready to be processed."""
    user_id: int
    chat_id: int
    urls: list[str]
    # The message to update with live pipeline progress
    status_message: Message


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["telegram_token"] = os.environ["TELEGRAM_TOKEN"]
    cfg["bot_password"] = os.environ["BOT_PASSWORD"]
    cfg.setdefault("steam_username", os.environ.get("STEAM_USERNAME", ""))
    return cfg


# --------------------------------------------------------------------------- #
# Auth helpers
# --------------------------------------------------------------------------- #

def _is_unlocked(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id in context.bot_data.setdefault("unlocked_users", set())


def _unlock(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data.setdefault("unlocked_users", set()).add(user_id)


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #

def _get_session(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> UserSession:
    sessions: dict[int, UserSession] = context.bot_data.setdefault("sessions", {})
    if user_id not in sessions:
        sessions[user_id] = UserSession()
    return sessions[user_id]


def _clear_session(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data.get("sessions", {}).pop(user_id, None)


# --------------------------------------------------------------------------- #
# Build queue worker (runs once as a background task)
# --------------------------------------------------------------------------- #

async def _build_worker(queue: asyncio.Queue, config: dict) -> None:
    """Single worker that processes BatchJobs one at a time."""
    while True:
        job: BatchJob = await queue.get()
        remaining = queue.qsize()
        logger.info(
            "Processing batch for user %s (%d URLs). Queue remaining: %d",
            job.user_id, len(job.urls), remaining,
        )

        loop = asyncio.get_running_loop()

        def pipeline_sync() -> None:
            for status_text in run_batch_pipeline(job.urls, config):
                logger.info("[pipeline] %s", status_text)
                future = asyncio.run_coroutine_threadsafe(
                    job.status_message.edit_text(status_text), loop
                )
                try:
                    future.result(timeout=10)
                except Exception as exc:
                    logger.warning("Could not edit status message: %s", exc)

        try:
            await asyncio.to_thread(pipeline_sync)
        except Exception as exc:
            logger.exception("Pipeline error for user %s", job.user_id)
            try:
                await job.status_message.edit_text(f"Erro no pipeline:\n{exc}")
            except Exception:
                pass

        queue.task_done()


# --------------------------------------------------------------------------- #
# Debounce: fires when a user goes idle for IDLE_TIMEOUT_SECONDS
# --------------------------------------------------------------------------- #

async def _session_timeout(
    user_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await asyncio.sleep(IDLE_TIMEOUT_SECONDS)

    session = _get_session(user_id, context)
    urls = list(session.urls)
    _clear_session(user_id, context)

    if not urls:
        return

    queue: asyncio.Queue = context.bot_data["build_queue"]
    position = queue.qsize() + 1  # +1 because currently processing job is not in queue

    n = len(urls)
    if position == 1:
        intro = f"Sessao encerrada! {n} musica(s) entrando no pipeline..."
    else:
        intro = (
            f"Sessao encerrada! {n} musica(s) na fila.\n"
            f"Aguardando {position - 1} build(s) anterior(es) terminar(em)..."
        )

    try:
        status_msg = await context.bot.send_message(chat_id=chat_id, text=intro)
    except Exception as exc:
        logger.warning("Could not send session-closed message: %s", exc)
        return

    await queue.put(BatchJob(
        user_id=user_id,
        chat_id=chat_id,
        urls=urls,
        status_message=status_msg,
    ))

    logger.info("User %s session closed: %d URL(s) queued (position %d)", user_id, n, position)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_unlocked(update.effective_user.id, context):
        await update.message.reply_text(
            "Ja autenticado! Envie links do YouTube ou Spotify.\n"
            f"Apos {IDLE_TIMEOUT_SECONDS}s sem enviar links, o batch e processado."
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
    queue: asyncio.Queue = context.bot_data["build_queue"]
    session = _get_session(update.effective_user.id, context)

    await update.message.reply_text(
        f"Status:\n"
        f"  Mod: {config.get('mod_id', '?')} ({config.get('media_type', 'cassette')})\n"
        f"  Workshop: {config.get('workshop_item_id') or 'nao configurado'}\n"
        f"  Builds na fila: {queue.qsize()}\n"
        f"  Links na sua sessao: {len(session.urls)}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: dict = context.bot_data["config"]
    user = update.effective_user
    text = (update.message.text or "").strip()

    # --- Auth ---
    if not _is_unlocked(user.id, context):
        if text == config["bot_password"]:
            _unlock(user.id, context)
            await update.message.reply_text(
                f"Senha correta! Pode enviar links do YouTube ou Spotify.\n"
                f"Apos {IDLE_TIMEOUT_SECONDS}s sem enviar, o batch e processado."
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

    session = _get_session(user.id, context)

    # Cancel existing idle timer
    if session.timer_task and not session.timer_task.done():
        session.timer_task.cancel()

    session.urls.append(text)
    n = len(session.urls)
    label = "playlist" if is_playlist_url(text) else "link"

    # Update or create the session status message
    session_text = (
        f"{n} {label}(s) na sessao.\n"
        f"Aguardando {IDLE_TIMEOUT_SECONDS}s sem novos links para processar..."
    )
    if session.status_message:
        try:
            await session.status_message.edit_text(session_text)
        except Exception:
            session.status_message = await update.message.reply_text(session_text)
    else:
        session.status_message = await update.message.reply_text(session_text)

    # Start new idle timer
    session.timer_task = asyncio.create_task(
        _session_timeout(user.id, update.effective_chat.id, context)
    )

    logger.info("User %s added URL (%d in session): %s", user.id, n, text)


# --------------------------------------------------------------------------- #
# Bot setup
# --------------------------------------------------------------------------- #

def build_application(config: dict) -> Application:
    app = Application.builder().token(config["telegram_token"]).build()
    app.bot_data["config"] = config

    queue: asyncio.Queue = asyncio.Queue()
    app.bot_data["build_queue"] = queue

    async def _start_worker(app: Application) -> None:
        asyncio.create_task(_build_worker(queue, config))

    app.post_init = _start_worker

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Simple Moozic Builder - Telegram Bot")
    parser.add_argument("--config", default="bot_config.json")
    ns = parser.parse_args()

    config_path = Path(ns.config)
    if not config_path.exists():
        print(f"Config nao encontrado: {config_path}", file=sys.stderr)
        print("Copie bot_config.json.example para bot_config.json.", file=sys.stderr)
        sys.exit(1)

    for env_key in ("TELEGRAM_TOKEN", "BOT_PASSWORD"):
        if not os.environ.get(env_key):
            print(f"Variavel de ambiente ausente: {env_key}", file=sys.stderr)
            print("Copie .env.example para .env e preencha.", file=sys.stderr)
            sys.exit(1)

    config = load_config(config_path)

    for key in ("mod_id", "work_dir"):
        if not config.get(key):
            print(f"Chave ausente no bot_config.json: {key}", file=sys.stderr)
            sys.exit(1)

    Path(config["work_dir"]).mkdir(parents=True, exist_ok=True)

    logger.info("Iniciando Moozic Bot para mod '%s'", config["mod_id"])
    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
