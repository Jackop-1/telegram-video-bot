# bot.py
import os
import asyncio
import tempfile
import shutil
import math
import time
import json
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import yt_dlp
import aiohttp
import aiofiles
import boto3

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Please set BOT_TOKEN env var")

CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "transfer").lower()  # "transfer" or "s3"
TELEGRAM_BOT_FILE_LIMIT = int(os.getenv("TG_FILE_LIMIT_MB", "50")) * 1024 * 1024  # default 50MB

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---------- Helpers ----------
def human_size(n):
    if n is None:
        return "unknown"
    step = 1024.0
    units = ["B","KB","MB","GB","TB"]
    i = 0
    while n >= step and i < len(units)-1:
        n /= step
        i += 1
    return f"{n:.1f}{units[i]}"

def safe_filename(name: str) -> str:
    # simple sanitize
    return "".join(c for c in name if c.isalnum() or c in " ._-()[]{}").strip()

# ---------- Format listing ----------
def build_formats_keyboard(formats):
    kb = InlineKeyboardMarkup(row_width=1)
    # choose unique formats, prefer higher resolution
    unique = {}
    for f in formats:
        fid = f.get("format_id")
        if not fid:
            continue
        # store best representative per format_id
        if fid not in unique:
            unique[fid] = f
        else:
            # if new one has higher quality, replace
            cur = unique[fid]
            if (f.get("height") or 0) > (cur.get("height") or 0):
                unique[fid] = f
    # sort by height desc, tbr desc
    sorted_f = sorted(unique.values(), key=lambda x: ((x.get("height") or 0), (x.get("tbr") or 0)), reverse=True)
    count = 0
    for f in sorted_f:
        if count >= 12:
            break
        fid = f.get("format_id")
        label_parts = []
        if f.get("height"):
            label_parts.append(f"{f['height']}p")
        if f.get("fps"):
            label_parts.append(f"{f['fps']}fps")
        if f.get("ext"):
            label_parts.append(f".{f['ext']}")
        # approximate filesize (sometimes provided)
        size = f.get("filesize") or f.get("filesize_approx")
        if size:
            label_parts.append(human_size(size))
        else:
            # show bitrate if available
            if f.get("tbr"):
                label_parts.append(f"{int(f['tbr'])}kbps")
        label = " ".join(label_parts) if label_parts else fid
        kb.add(InlineKeyboardButton(label, callback_data=f"dl|{fid}"))
        count += 1
    # add audio mp3 option
    kb.add(InlineKeyboardButton("Audio (MP3)", callback_data="dl|audio_mp3"))
    return kb

# ---------- yt-dlp progress hooks ----------
class DownloadProgress:
    def __init__(self, edit_message_func):
        self.last_edit = 0.0
        self.total_bytes = None
        self.edit_message = edit_message_func
        self.start_time = time.time()

    def hook(self, d):
        # d is progress dict from yt-dlp
        status = d.get("status")
        now = time.time()
        # update roughly every 0.8 sec or on completion
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta")
            percent = (downloaded / total * 100) if total else 0.0
            if now - self.last_edit > 0.8:
                text = f"Downloading: {percent:.1f}% ({human_size(downloaded)} / {human_size(total)})\nSpeed: {human_size(speed)}/s\nETA: {int(eta) if eta else '-'}s"
                asyncio.create_task(self.edit_message(text))
                self.last_edit = now
        elif status == "finished":
            asyncio.create_task(self.edit_message("Download finished. Processing..."))
        elif status == "error":
            asyncio.create_task(self.edit_message("Download error."))

# ---------- cloud upload helpers ----------
async def upload_to_transfersh(path: str, edit_message_func):
    filename = Path(path).name
    url = None
    size = Path(path).stat().st_size
    uploaded = 0

    async with aiohttp.ClientSession() as session:
        # streaming upload with progress
        async with session.put(f"https://transfer.sh/{filename}", data=stream_file_with_progress(path, lambda n: asyncio.create_task(edit_message_func(f"Uploading to cloud: {human_size(n)} / {human_size(size)}")))) as resp:
            if resp.status in (200,201):
                url = await resp.text()
                return url.strip()
            else:
                txt = await resp.text()
                raise RuntimeError(f"transfer.sh upload failed: {resp.status} {txt}")

def stream_file_with_progress(path, progress_callback):
    # returns an async iterator suitable for aiohttp data parameter
    async def _gen():
        chunk_size = 1024 * 64
        sent = 0
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                progress_callback(sent)
                yield chunk
    return _gen()

def upload_to_s3(path, bucket, key, edit_message_func, region=None):
    # synchronous boto3 upload with progress, run in executor
    class Progress:
        def __init__(self, edit_fn, total):
            self._seen = 0
            self.total = total
            self.edit_fn = edit_fn
            self.last_time = time.time()
        def __call__(self, bytes_amount):
            self._seen += bytes_amount
            now = time.time()
            if now - self.last_time > 0.8 or self._seen == self.total:
                asyncio.get_event_loop().create_task(self.edit_fn(f"Uploading to S3: {human_size(self._seen)} / {human_size(self.total)}"))
                self.last_time = now

    s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    total = Path(path).stat().st_size
    prog = Progress(lambda t: bot.send_message(chat_id=edit_message_func.chat_id, text=t), total)  # fallback, not perfect
    s3.upload_file(path, bucket, key, Callback=prog)
    # construct URL (public object expected)
    url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}" if region else f"https://{bucket}.s3.amazonaws.com/{key}"
    return url

# ---------- Handlers ----------
@dp.message_handler(commands=["start","help"])
async def cmd_start(msg: types.Message):
    await msg.reply("Send me a YouTube/Instagram/Facebook link. I'll list formats (video + MP3). Choose a format to download.")

@dp.message_handler()
async def handle_url(msg: types.Message):
    url = msg.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await msg.reply("Please send a valid http/https URL.")
        return

    info_msg = await msg.reply("Fetching video info... ⏳")
    loop = asyncio.get_event_loop()

    def fetch_info():
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await loop.run_in_executor(None, fetch_info)
    except Exception as e:
        await info_msg.edit_text(f"Failed to fetch info: {e}")
        return

    title = info.get("title","video")
    thumbnail = info.get("thumbnail")
    formats = info.get("formats", [])
    if not formats:
        await info_msg.edit_text("No downloadable formats found.")
        return

    kb = build_formats_keyboard(formats)
    caption = f"Title: <b>{title}</b>\nChoose a format below:"
    if thumbnail:
        try:
            await bot.send_photo(chat_id=msg.chat.id, photo=thumbnail, caption=caption, parse_mode="HTML", reply_markup=kb)
            await info_msg.delete()
            return
        except Exception:
            # fallback to editing message
            pass

    await info_msg.edit_text(caption, parse_mode="HTML", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("dl|"))
async def on_format_choice(callback: types.CallbackQuery):
    await callback.answer()
    choice = callback.data.split("|",1)[1]
    # attempt to find original url from the message thread
    # Prefer the previous message from user
    origin = None
    # try message.reply_to_message
    if callback.message.reply_to_message and callback.message.reply_to_message.text:
        origin = callback.message.reply_to_message.text.strip()
    else:
        # fallback: scan last 12 messages in chat (best-effort) - NOTE: may not have permission
        origin = None

    if not origin or not origin.startswith("http"):
        # ask user to resend
        await callback.message.reply("Original URL not found in the thread. Please send the link again and choose format.")
        return

    status_msg = await bot.send_message(chat_id=callback.message.chat.id, text="Starting...")

    tmpdir = tempfile.mkdtemp(prefix="tgdl_")
    try:
        # prepare download options
        ydl_opts = {
            "outtmpl": os.path.join(tmpdir, "%(title).200s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [],
        }

        # progress hook object
        progress = DownloadProgress(lambda text: status_msg.edit_text(text))
        ydl_opts["progress_hooks"].append(progress.hook)

        if choice == "audio_mp3":
            # extract audio and convert to mp3
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            ydl_opts["format"] = choice

        # run yt-dlp in executor to avoid blocking
        loop = asyncio.get_event_loop()
        def run_ytdlp():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(origin, download=True)
                return info

        try:
            info = await loop.run_in_executor(None, run_ytdlp)
        except Exception as e:
            await status_msg.edit_text(f"Download failed: {e}")
            return

        # find produced file
        files = list(Path(tmpdir).glob("*"))
        if not files:
            await status_msg.edit_text("No file produced by downloader.")
            return
        # pick largest file (likely desired)
        files_sorted = sorted(files, key=lambda p: p.stat().st_size, reverse=True)
        file_path = str(files_sorted[0])
        file_size = Path(file_path).stat().st_size
        fname = Path(file_path).name

        # if file too big for Telegram bot direct upload
        if file_size > TELEGRAM_BOT_FILE_LIMIT:
            await status_msg.edit_text(f"File is large ({human_size(file_size)}). Uploading to cloud...")

            if CLOUD_PROVIDER == "transfer":
                try:
                    url = await upload_to_transfersh(file_path, lambda text: status_msg.edit_text(text))
                    await status_msg.edit_text(f"Uploaded to transfer.sh:\n{url}\nYou can download from that link.")
                    return
                except Exception as e:
                    await status_msg.edit_text(f"Cloud upload failed: {e}")
                    return
            elif CLOUD_PROVIDER == "s3":
                # run blocking s3 upload in executor
                bucket = os.getenv("AWS_S3_BUCKET")
                region = os.getenv("AWS_REGION")
                if not bucket:
                    await status_msg.edit_text("S3 bucket not configured (AWS_S3_BUCKET env var missing).")
                    return
                key = f"tgdl/{time.time_ns()}_{fname}"
                try:
                    def s3_runner():
                        s3 = boto3.client("s3")
                        s3.upload_file(file_path, bucket, key)
                        return True
                    await loop.run_in_executor(None, s3_runner)
                    url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}" if region else f"https://{bucket}.s3.amazonaws.com/{key}"
                    await status_msg.edit_text(f"Uploaded to S3:\n{url}")
                    return
                except Exception as e:
                    await status_msg.edit_text(f"S3 upload failed: {e}")
                    return
            else:
                await status_msg.edit_text("Unknown CLOUD_PROVIDER configured.")
                return
        else:
            # Try to send file to Telegram
            await status_msg.edit_text(f"Preparing to send {fname} ({human_size(file_size)}) to Telegram...")
            ext = Path(file_path).suffix.lower()
            try:
                if ext in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
                    await bot.send_video(chat_id=callback.message.chat.id, video=open(file_path, "rb"), caption=fname)
                elif ext in [".mp3", ".m4a", ".wav", ".aac", ".ogg"]:
                    await bot.send_audio(chat_id=callback.message.chat.id, audio=open(file_path, "rb"), caption=fname)
                else:
                    await bot.send_document(chat_id=callback.message.chat.id, document=open(file_path, "rb"), caption=fname)
                await status_msg.edit_text("Done ✅")
            except Exception as e:
                await status_msg.edit_text(f"Failed to send file to Telegram: {e}\nAs fallback, uploading to cloud...")
                # fallback: upload to transfer.sh
                try:
                    url = await upload_to_transfersh(file_path, lambda text: status_msg.edit_text(text))
                    await status_msg.edit_text(f"Uploaded to transfer.sh:\n{url}")
                    return
                except Exception as e2:
                    await status_msg.edit_text(f"Cloud upload also failed: {e2}")
                    return

    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

if __name__ == "__main__":
    print("Bot starting...")
    executor.start_polling(dp, skip_updates=True)
