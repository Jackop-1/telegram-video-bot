import os
import yt_dlp
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# ======================================
# ⚙️ Apna Telegram Bot ka token aur chat ID yahan daale
BOT_TOKEN = "8460161841:AAFfN2y1v9hot2zzkfADIjy-pvioYtapMPM"   # BotFather se mila token
OWNER_ID = "6341508001"                     # Apna Telegram user ID (int me)
# ======================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# 🧠 Helper function: Progress hook for download
def progress_hook(d):
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '').strip()
        print(f"Downloading: {percent}")
    elif d['status'] == 'finished':
        print("Download complete, now converting...")

# 🎬 Command: /start
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.reply("👋 Send me a YouTube / Instagram / Facebook link to download the video.\n\nYou can also convert to MP3!")

# 📥 Video download handler
@dp.message_handler(content_types=['text'])
async def handle_link(message: types.Message):
    url = message.text.strip()

    # Basic link validation
    if not any(domain in url for domain in ["youtube.com", "youtu.be", "instagram.com", "facebook.com", "fb.watch"]):
        await message.reply("❌ Please send a valid YouTube, Instagram, or Facebook link.")
        return

    msg = await message.reply("⏳ Fetching formats...")

    ydl_opts = {
        'progress_hooks': [progress_hook],
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video')
            formats = [
                f"{f.get('format_id')} - {f.get('ext')} - {f.get('resolution', 'audio only')}"
                for f in info['formats'] if f.get('ext') in ['mp4', 'm4a', 'webm']
            ]

        keyboard = types.InlineKeyboardMarkup()
        for f in info['formats']:
            if f.get('ext') == 'mp4' and f.get('height'):
                btn_text = f"🎥 {f['height']}p"
                keyboard.add(types.InlineKeyboardButton(btn_text, callback_data=f"video|{url}|{f['format_id']}"))

        keyboard.add(types.InlineKeyboardButton("🎧 Download MP3", callback_data=f"audio|{url}"))
        await msg.edit_text(f"🎬 *{title}*\nSelect quality or MP3:", parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ⚙️ Download video or audio on button click
@dp.callback_query_handler(lambda c: c.data)
async def process_callback(callback_query: types.CallbackQuery):
    action, url, *rest = callback_query.data.split('|')
    await bot.answer_callback_query(callback_query.id)
    msg = await bot.send_message(callback_query.from_user.id, "⬇️ Downloading, please wait...")

    fmt_id = rest[0] if rest else None
    filename = None

    try:
        ydl_opts = {
            'progress_hooks': [progress_hook],
            'outtmpl': 'downloads/%(title)s.%(ext)s'
        }

        if action == "audio":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        elif action == "video" and fmt_id:
            ydl_opts['format'] = fmt_id

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if action == "audio":
                filename = filename.rsplit('.', 1)[0] + ".mp3"

        await msg.edit_text("📤 Uploading...")
        with open(filename, 'rb') as f:
            await bot.send_document(callback_query.from_user.id, f)

        await msg.edit_text("✅ Done!")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# 🧩 Start bot
if __name__ == '__main__':
    print("🚀 Bot is running...")
    asyncio.run(dp.start_polling())
