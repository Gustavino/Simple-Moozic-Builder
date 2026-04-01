# Simple Moozic Builder — Telegram Bot Setup

This guide explains everything you need to configure and run the Telegram bot
that adds music to the Project Zomboid Moozic mod automatically.

---

## What you need to provide

### 1. Telegram Bot Token

1. Open Telegram and talk to **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token (looks like `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

Put it in `.env` as `TELEGRAM_TOKEN`.

---

### 2. Bot Password

A single passphrase that any user must type once to unlock music submission.
Any string works — something hard to guess.

Put it in `.env` as `BOT_PASSWORD`.

---

### 3. Steam Workshop Item ID

Your mod already exists on Steam Workshop. To find the item ID:

1. Open the mod's Steam Workshop page in a browser
2. Look at the URL: `https://steamcommunity.com/sharedfiles/filedetails/?id=XXXXXXXXXX`
3. Copy the number after `?id=`

Put it in `bot_config.json` as `"workshop_item_id": "XXXXXXXXXX"`.

---

### 4. Steam Account (for Workshop upload)

The bot uses **steamcmd** to update the Workshop item. steamcmd must run as the
Steam account that owns the mod.

#### One-time credential cache (run this on the VM once, manually):

```bash
steamcmd +login YOUR_STEAM_USERNAME YOUR_STEAM_PASSWORD +quit
```

After this, steamcmd caches the credentials locally and the bot can upload
without a password each time.

Put your Steam username in `.env` as `STEAM_USERNAME`.

> If you use Steam Guard (2FA), you will be prompted for the code during the
> one-time cache step above. After that, steamcmd uses the cached session.

---

### 5. Mod ID and Name

These are the SMB identifiers for your mod:

- `mod_id` — the folder/module name used in PZ scripts (e.g. `TaliMix`)
- `mod_name` — the display name shown in Workshop (e.g. `Tali Mix`)

Put both in `bot_config.json`.

---

### 6. Work Directory

A folder on the VM where the bot stores:
- Downloaded audio files (`downloads/`)
- Converted `.ogg` files (`_ogg/`)
- Built mod output (`OUTPUT/`)

Example: `/home/user/moozic_bot_workspace`

Put it in `bot_config.json` as `"work_dir"`.

---

## VM Setup (Ubuntu/Debian)

```bash
# 1. System packages
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip ffmpeg

# 2. steamcmd
sudo apt install -y steamcmd

# 3. Clone the repo (if not already)
git clone https://github.com/gustavino/simple-moozic-builder.git
cd simple-moozic-builder

# 4. Python virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 5. Install all dependencies
pip install -r requirements.txt

# 6. Cache steamcmd credentials (one time)
steamcmd +login YOUR_STEAM_USERNAME YOUR_STEAM_PASSWORD +quit
```

---

## Configuration files

### `.env`  ← secrets, never commit this

```env
TELEGRAM_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BOT_PASSWORD=minha_senha_secreta
STEAM_USERNAME=your_steam_username
```

### `bot_config.json`  ← non-secret settings

```json
{
    "mod_id": "TaliMix",
    "mod_name": "Tali Mix",
    "author": "Tali",
    "parent_mod_id": "TrueMoozic",
    "standalone_bundle": false,
    "media_type": "cassette",
    "work_dir": "/home/user/moozic_bot_workspace",
    "assets_root": "",
    "workshop_cover": "",
    "workshop_item_id": "XXXXXXXXXX",
    "steamcmd_path": "steamcmd"
}
```

> `assets_root` and `workshop_cover` can be left empty — the bot uses the
> bundled assets from the repo and generates a cover from the song thumbnail.

---

## Running the bot

```bash
source .venv/bin/activate
python -m bot.telegram_bot --config bot_config.json
```

To keep it running after you close the terminal:

```bash
nohup python -m bot.telegram_bot --config bot_config.json > bot.log 2>&1 &
```

Or use **systemd** (recommended for a VM):

```ini
# /etc/systemd/system/moozic-bot.service
[Unit]
Description=Simple Moozic Builder Telegram Bot
After=network.target

[Service]
User=user
WorkingDirectory=/home/user/simple-moozic-builder
ExecStart=/home/user/simple-moozic-builder/.venv/bin/python -m bot.telegram_bot --config bot_config.json
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now moozic-bot
```

---

## How the bot works (user flow)

1. User opens the bot on Telegram
2. User types the `BOT_PASSWORD` → unlocked for the session
3. User pastes a YouTube or Spotify link
4. Bot replies with live status updates:
   - Downloading...
   - Converting to .ogg...
   - Building mod...
   - Uploading to Workshop...
   - Done!
5. Players update the mod in the PZ Workshop and the song appears in the game

---

## Supported URL formats

| Source  | Example |
|---------|---------|
| YouTube | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| YouTube | `https://youtu.be/dQw4w9WgXcQ` |
| Spotify | `https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC` |

Playlists are not supported — one track at a time.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `TELEGRAM_TOKEN not set` | Check your `.env` file is in the repo root |
| `yt-dlp: command not found` | `pip install yt-dlp` inside the venv |
| `spotdl: command not found` | `pip install spotdl` inside the venv |
| `steamcmd: command not found` | `sudo apt install steamcmd` |
| steamcmd asks for 2FA code | Re-run the one-time cache command and enter the code |
| `No .ogg files found` | Check that ffmpeg is installed: `ffmpeg -version` |
