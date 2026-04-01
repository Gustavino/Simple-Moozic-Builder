# Simple Moozic Builder — Claude Code Context

## O que é este projeto

Ferramenta para criar mods de música para **Project Zomboid (Build 42)** no ecossistema **TrueMoozic**.
O builder gera itens de cassete e vinil, scripts Lua, texturas e estrutura pronta para a Steam Workshop.

O branch `claude/telegram-music-bot-wDPcO` adiciona um **bot do Telegram** que automatiza o workflow completo:
usuário envia link → bot baixa → converte → rebuilda o mod → faz upload na Workshop.

---

## Arquivos principais

| Arquivo | Função |
|---|---|
| `simple_moozic_builder.py` | Core do SMB: build, conversão de áudio, geração de assets |
| `simple_moozic_builder_ui.py` | UI desktop (customtkinter) — não usada pelo bot |
| `bot/downloader.py` | Download via yt-dlp (YouTube) e spotdl (Spotify) |
| `bot/steam_uploader.py` | Upload para Workshop via steamcmd |
| `bot/pipeline.py` | Orquestra download → conversão → build → upload |
| `bot/telegram_bot.py` | Entry point do bot, handlers Telegram, auth por senha |
| `bot_config.json` | Config não-secreta do bot (não commitado, ver exemplo) |
| `.env` | Segredos: TELEGRAM_TOKEN, BOT_PASSWORD, STEAM_USERNAME (não commitado) |
| `SETUP_BOT.md` | Guia completo de setup da VM |

---

## Como rodar o bot

```bash
source .venv/bin/activate
python -m bot.telegram_bot --config bot_config.json
```

Ver logs do systemd service:
```bash
journalctl -u moozic-bot -f
```

Reiniciar o service:
```bash
systemctl restart moozic-bot
```

---

## Funções SMB usadas pelo bot (não modificar a assinatura)

```python
# Converte um arquivo de áudio para .ogg
convert_single_audio_file(source_file: Path, audio_dir: Path, force: bool) -> AudioTrackEntry

# Builda o mod completo a partir de todos os .ogg em audio_dir
build_mod_from_config(config: dict, on_track=None) -> Path

# Paths padrão usados quando assets_root não está configurado
default_assets_root() -> Path
```

O `build_mod_from_config` rebuilda o mod inteiro a cada música nova.
Todos os `.ogg` acumulados em `work_dir/_ogg/` entram no build.

---

## Estrutura do work_dir (gerada pelo bot)

```
{work_dir}/
  downloads/      # áudios baixados (mp3, thumbnails)
  _ogg/           # cache de conversões .ogg (persiste entre builds)
  OUTPUT/         # mod buildado, pronto para upload na Workshop
```

---

## Configuração

**`.env`** — segredos:
```
TELEGRAM_TOKEN=...
BOT_PASSWORD=...
STEAM_USERNAME=...
```

**`bot_config.json`** — settings:
```json
{
  "mod_id": "TaliMix",
  "mod_name": "Tali Mix",
  "author": "Tali",
  "parent_mod_id": "TrueMoozic",
  "media_type": "cassette",
  "work_dir": "/home/user/moozic_bot_workspace",
  "workshop_item_id": "XXXXXXXXXX",
  "steamcmd_path": "steamcmd"
}
```

Copiar exemplos:
```bash
cp .env.example .env
cp bot_config.json.example bot_config.json
```

---

## Dependências do sistema (Ubuntu/Debian)

```bash
apt install -y python3.12 python3.12-venv ffmpeg steamcmd
```

Python packages:
```bash
pip install -r requirements.txt
```

Principais adições do bot: `python-telegram-bot`, `yt-dlp`, `spotdl`, `python-dotenv`.

---

## steamcmd — cache de credenciais (rodar uma vez)

```bash
steamcmd +login SEU_USER SUA_SENHA +quit
```

Depois disso o bot faz upload sem precisar de senha.

---

## Problemas comuns

| Problema | Causa | Fix |
|---|---|---|
| `No .ogg files found` | ffmpeg não instalado | `apt install ffmpeg` |
| `yt-dlp: not found` | fora do venv | `source .venv/bin/activate` |
| `TELEGRAM_TOKEN not set` | `.env` não existe ou não foi carregado | verificar `.env` na raiz do repo |
| steamcmd pede 2FA | credenciais não cacheadas | rodar o cache manual acima |
| Bot para de responder | exception no pipeline | `journalctl -u moozic-bot -f` |

---

## Fluxo de autenticação do bot

1. Qualquer usuário abre o bot
2. Digita `BOT_PASSWORD` → sessão desbloqueada
3. Envia link YouTube/Spotify → pipeline roda
4. Sessão reseta quando o bot reinicia
