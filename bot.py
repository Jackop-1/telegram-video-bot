# bot.py — Public Telegram Downloader (Replit version, fixed)
# Supports YouTube / Instagram / Facebook + MP3 + transfer.sh
# Author: Jackop + ChatGPT 💥

import os
import asyncio
import tempfile
import shutil
import time
from pathlib import Path
import logging
import nest_asyncio  # 🩵 Replit async fix
nest_asyncio.apply()

# auto update yt-dlp
os.system("pip install -U yt-dlp > /dev/null 2>&1")

import yt_dlp
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN missing! Add it in Replit Secrets.")

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB
logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---------------- HELPERS ----------------
def human_size(n):
    if not n:
        return "unknown"
    step = 1024.0
    units = ["B", "KB", "MB", "GB"]
    i = 0
    while n >= step and i < len(units) - 1:
        n /= step
        i += 1
    return f"{n:.1f}{units[i]}"

def sanitize_url(text):
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
        if fid not in unique or (f.get("height") or 0) > (unique[fid].get("height") or 0):
            unique[fid] = f
    sorted_formats = sorted(unique.values(), key=lambda x: (x.get("height") or 0), reverse=True)
    for f in sorted_formats[:10]:
        fid = f.get("format_id")
        label = f"{f.get('height','?')}p .{f.get('ext','?')} ({human_size(f.get('filesize') or f.get('filesize_approx'))})"
        kb.add(InlineKeyboardButton(label, callback_data=f"dl|{fid}"))
    kb.add(InlineKeyboardButton("🎵 MP3 Audio", callback_data="dl|audio_mp3"))
    return kb

class ProgressHook:
    def __init__(self, edit_func):
        self.last = 0
        self.edit = edit_func

    def __call__(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0
            if time.time() - self.last > 1:
                asyncio.create_task(self.edit(f"⬇️ Downloading... {percent:.1f}%"))
                self.last = time.time()
        elif d["status"] == "finished":
            asyncio.create_task(self.edit("⚙️ Processing file..."))

async def upload_transfer(file_path, status_edit):
    filename = Path(file_path).name
    async with aiohttp.ClientSession() as session:
        async with session.put(f"https://transfer.sh/{filename}", data=open(file_path, "rb")) as resp:
            if resp.status == 200:
                link = (await resp.text()).strip()
                await status_edit(f"🔗 Uploaded to transfer.sh:\n{link}")
                return link
            else:
                await status_edit(f"❌ Upload failed ({resp.status})")
                return None

# ---------------- HANDLERS ----------------
@dp.message_handler(commands=["start", "help"])
async def start(msg: types.Message):
    await msg.answer(
        "👋 Hi! Send me any YouTube / Instagram / Facebook link.\n"
        "You can choose HD or MP3 format, and I’ll send it to you 🎬🎵"
    )

@dp.message_handler()
async def handle_link(msg: types.Message):
    url = sanitize_url(msg.text)
    if not url.startswith("http"):
        await msg.answer("❌ Please send a valid link.")
        return

    info_msg = await msg.answer("🔍 Fetching video info...")

    loop = asyncio.get_event_loop()

    def fetch_info():
        opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await loop.run_in_executor(None, fetch_info)
        formats = info.get("formats", [])
        kb = build_formats_keyboard(formats)
        caption = f"🎬 <b>{info.get('title','Video')}</b>\nSelect a format below:"
        await info_msg.edit_text(caption, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        await info_msg.edit_text(f"❌ Error fetching info: {e}")

    dp.last_url = url

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("dl|"))
async def on_download(cq: types.CallbackQuery):
    await cq.answer()
    data = cq.data.split("|", 1)[1]
    url = getattr(dp, "last_url", None)
    if not url:
        await cq.message.answer("❌ Please send the link again.")
        return

    status = await cq.message.answer("📥 Starting download...")
    tmpdir = tempfile.mkdtemp(prefix="dl_")

    try:
        opts = {
            "outtmpl": os.path.join(tmpdir, "%(title).200s.%(ext)s"),
            "quiet": True,
            "progress_hooks": [ProgressHook(lambda t: status.edit_text(t))],
        }

        if data == "audio_mp3":
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            opts["format"] = data

        loop = asyncio.get_event_loop()

        def run_dl():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await loop.run_in_executor(None, run_dl)
        files = list(Path(tmpdir).glob("*"))
        if not files:
            await status.edit_text("❌ No file found after download.")
            return
        file_path = str(max(files, key=lambda p: p.stat().st_size))
        fsize = Path(file_path).stat().st_size

        if fsize > TELEGRAM_FILE_LIMIT:
            await status.edit_text(f"⚠️ File too large ({human_size(fsize)}), uploading to transfer.sh...")
            link = await upload_transfer(file_path, lambda t: status.edit_text(t))
            if link:
                await status.edit_text(f"✅ Done!\n{link}")
            return

        ext = Path(file_path).suffix.lower()
        await status.edit_text("📤 Uploading to Telegram...")

        if ext in [".mp4", ".mkv", ".mov", ".webm"]:
            await bot.send_video(cq.from_user.id, open(file_path, "rb"), caption=Path(file_path).name)
        elif ext in [".mp3", ".m4a", ".wav"]:
            await bot.send_audio(cq.from_user.id, open(file_path, "rb"), caption=Path(file_path).name)
        else:
            await bot.send_document(cq.from_user.id, open(file_path, "rb"), caption=Path(file_path).name)

        await status.edit_text("✅ Done!")
    except Exception as e:
        await status.edit_text(f"❌ Error: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ---------------- RUN ----------------
if __name__ == "__main__":
    print("🚀 Bot running — Public Mode (Replit Optimized)")
    executor.start_polling(dp, skip_updates=True)
