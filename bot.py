# bot.py — Replit optimized Telegram downloader (yt-dlp + MP3 + progress + transfer.sh fallback)
# 1) Put your BOT token into Replit Secrets as BOT_TOKEN
# 2) Run the repl. On start it will update yt-dlp.
# 3) Send a YouTube/Instagram/Facebook link to the bot and pick a format / MP3.

import os
import asyncio
import tempfile
import shutil
import time
from pathlib import Path
import math
import logging

# ensure latest yt-dlp on start (fixes many "format" errors)
os.system("pip install -U yt-dlp > /dev/null 2>&1")

import yt_dlp
import aiohttp
import aiofiles
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set. Add it to Replit Secrets (Tools -> Secrets).")

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB approximate bot limit
CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "transfer").lower()  # only 'transfer' supported here

# ---------------- Setup ----------------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---------------- Helpers ----------------
def human_size(n):
    if not n:
        return "unknown"
    step = 1024.0
    units = ["B","KB","MB","GB","TB"]
    i = 0
    while n >= step and i < len(units)-1:
        n /= step
        i += 1
    return f"{n:.1f}{units[i]}"

def sanitize_url(text: str) -> str:
    # get first token and strip spaces
    if not text:
        return ""
    return text.strip().split()[0]

def build_formats_keyboard(formats):
    kb = InlineKeyboardMarkup(row_width=1)
    unique = {}
    for f in formats:
        fid = f.get("format_id")
        if not fid:
            continue
        # keep best rep for format id
        if fid not in unique or (f.get("height") or 0) > (unique[fid].get("height") or 0):
            unique[fid] = f
    sorted_formats = sorted(unique.values(), key=lambda x: ((x.get("height") or 0), (x.get("tbr") or 0)), reverse=True)
    count = 0
    for f in sorted_formats:
        if count >= 12:
            break
        fid = f.get("format_id")
        parts = []
        if f.get("height"):
            parts.append(f"{f['height']}p")
        if f.get("fps"):
            parts.append(f"{f['fps']}fps")
        if f.get("ext"):
            parts.append(f".{f['ext']}")
        size = f.get("filesize") or f.get("filesize_approx")
        if size:
            parts.append(human_size(size))
        elif f.get("tbr"):
            parts.append(f"{int(f['tbr'])}kbps")
        label = " ".join(parts) if parts else fid
        kb.add(InlineKeyboardButton(label, callback_data=f"dl|{fid}"))
        count += 1
    # audio option
    kb.add(InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl|audio_mp3"))
    return kb

# progress hook helper class
class ProgressHook:
    def __init__(self, edit_func):
        self.last = 0
        self.edit = edit_func
        self.start = time.time()
    def __call__(self, d):
        status = d.get("status")
        now = time.time()
        try:
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                eta = d.get("eta")
                percent = (downloaded / total * 100) if total else None
                if now - self.last > 0.8:
                    text = f"⬇️ Downloading: {percent:.1f}%\n{human_size(downloaded)} / {human_size(total)}\nSpeed: {human_size(speed)}/s\nETA: {int(eta) if eta else '-'}s"
                    asyncio.create_task(self.edit(text))
                    self.last = now
            elif status == "finished":
                asyncio.create_task(self.edit("⚙️ Download finished — processing..."))
            elif status == "error":
                asyncio.create_task(self.edit("❌ Download error"))
        except Exception:
            pass

# transfer.sh upload with progress
async def upload_to_transfersh(path: str, status_edit):
    filename = Path(path).name
    size = Path(path).stat().st_size
    status_called = 0
    async def _gen():
        nonlocal status_called
        chunk = 64 * 1024
        sent = 0
        async with aiofiles.open(path, "rb") as f:
            while True:
                data = await f.read(chunk)
                if not data:
                    break
                sent += len(data)
                if time.time() - status_called > 0.8:
                    await status_edit(f"⬆️ Uploading to transfer.sh: {human_size(sent)} / {human_size(size)}")
                    status_called = time.time()
                yield data
    async with aiohttp.ClientSession() as session:
        put_url = f"https://transfer.sh/{filename}"
        async with session.put(put_url, data=_gen()) as resp:
            if resp.status in (200,201):
                text = (await resp.text()).strip()
                return text
            else:
                raise RuntimeError(f"transfer.sh failed: {resp.status} {(await resp.text())}")

# ---------------- Handlers ----------------
@dp.message_handler(commands=["start","help"])
async def cmd_start(msg: types.Message):
    await msg.reply("Send a YouTube / Instagram / Facebook link and I'll list download formats + MP3 option.")

@dp.message_handler()
async def handle_message(msg: types.Message):
    text = msg.text or ""
    url = sanitize_url(text)
    if not (url.startswith("http://") or url.startswith("https://")):
        await msg.reply("Please send a valid http/https URL.")
        return

    info_msg = await msg.reply("🔎 Fetching available formats...")
    loop = asyncio.get_event_loop()
    def fetch_info():
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    try:
        info = await loop.run_in_executor(None, fetch_info)
    except Exception as e:
        await info_msg.edit_text(f"❌ Failed to fetch info: {str(e)[:300]}")
        return

    title = info.get("title", "video")
    formats = info.get("formats", [])
    if not formats:
        await info_msg.edit_text("No formats found.")
        return

    kb = build_formats_keyboard(formats)
    # try sending thumbnail with keyboard if exists
    thumb = info.get("thumbnail")
    caption = f"🎬 <b>{title}</b>\nChoose a format:"
    try:
        if thumb:
            await bot.send_photo(chat_id=msg.chat.id, photo=thumb, caption=caption, parse_mode="HTML", reply_markup=kb)
            await info_msg.delete()
            return
    except Exception:
        pass

    await info_msg.edit_text(caption, parse_mode="HTML", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("dl|"))
async def on_choice(cq: types.CallbackQuery):
    await cq.answer()
    data = cq.data.split("|",1)[1]
    # find original URL: prefer the previous message from user (reply_to) or in chat history
    origin = None
    if cq.message.reply_to_message and cq.message.reply_to_message.text:
        origin = sanitize_url(cq.message.reply_to_message.text)
    else:
        # fallback: try looking back few messages in chat (best-effort)
        origin = None
    if not origin:
        await cq.message.reply("Original URL not found in thread. Please send the link again and choose a format.")
        return

    status = await bot.send_message(cq.from_user.id, "Starting...")
    tmpdir = tempfile.mkdtemp(prefix="tgdl_")
    try:
        # prepare yt-dlp options
        ydl_opts = {
            "outtmpl": os.path.join(tmpdir, "%(title).200s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": []
        }
        progress = ProgressHook(lambda text: status.edit_text(text))
        ydl_opts["progress_hooks"].append(progress)

        if data == "audio_mp3":
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            ydl_opts["format"] = data

        loop = asyncio.get_event_loop()
        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(origin, download=True)
        try:
            info = await loop.run_in_executor(None, run_download)
        except Exception as e:
            await status.edit_text(f"❌ Download failed: {str(e)[:300]}")
            return

        # find produced file
        produced = list(Path(tmpdir).glob("*"))
        if not produced:
            await status.edit_text("❌ No file produced by yt-dlp.")
            return
        produced_sorted = sorted(produced, key=lambda p: p.stat().st_size, reverse=True)
        file_path = str(produced_sorted[0])
        fsize = Path(file_path).stat().st_size
        fname = Path(file_path).name

        if fsize > TELEGRAM_FILE_LIMIT:
            await status.edit_text(f"⚠️ File too large ({human_size(fsize)}). Uploading to transfer.sh...")
            try:
                url = await upload_to_transfersh(file_path, lambda t: status.edit_text(t))
                await status.edit_text(f"🔗 Uploaded: {url}")
                return
            except Exception as e:
                await status.edit_text(f"❌ Cloud upload failed: {str(e)[:300]}")
                return
        else:
            await status.edit_text(f"📤 Sending {fname} ({human_size(fsize)}) to Telegram...")
            ext = Path(file_path).suffix.lower()
            try:
                if ext in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
                    await bot.send_video(chat_id=cq.from_user.id, video=open(file_path,"rb"), caption=fname)
                elif ext in [".mp3", ".m4a", ".wav", ".ogg", ".aac"]:
                    await bot.send_audio(chat_id=cq.from_user.id, audio=open(file_path,"rb"), caption=fname)
                else:
                    await bot.send_document(chat_id=cq.from_user.id, document=open(file_path,"rb"), caption=fname)
                await status.edit_text("✅ Done!")
            except Exception as e:
                await status.edit_text(f"❌ Sending failed: {str(e)[:300]}\nAttempting cloud upload...")
                try:
                    url = await upload_to_transfersh(file_path, lambda t: status.edit_text(t))
                    await status.edit_text(f"🔗 Uploaded: {url}")
                    return
                except Exception as e2:
                    await status.edit_text(f"❌ Both sending and cloud upload failed: {str(e2)[:200]}")
                    return
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

# ---------------- Start ----------------
if __name__ == "__main__":
    print("🚀 Bot is starting...")
    executor.start_polling(dp, skip_updates=True)
