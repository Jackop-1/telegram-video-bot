import os
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import asyncio

# ✅ Get token and owner ID from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable not set!")

# Logging setup
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.answer("👋 Send me any YouTube / Instagram / Facebook video link to download.")


@dp.message_handler(lambda message: message.text.startswith("http"))
async def download_video(message: types.Message):
    url = message.text.strip()
    await message.reply("⏳ Fetching available formats...")

    try:
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown Title')
            formats = [
                (f"{f['format_note']} - {round(f['filesize'] / 1024 / 1024, 2)} MB" if f.get('filesize') else f['format_note'], f['format_id'])
                for f in info['formats'] if f.get('ext') == 'mp4'
            ]
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            for text, format_id in formats[-6:]:
                keyboard.insert(InlineKeyboardButton(text, callback_data=f"dl|{format_id}|{url}"))
            keyboard.add(InlineKeyboardButton("🎵 MP3", callback_data=f"mp3|{url}"))

            await message.reply(f"🎬 **{title}**\nSelect format:", reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@dp.callback_query_handler(lambda c: c.data.startswith("dl|"))
async def callback_download(callback_query: types.CallbackQuery):
    _, format_id, url = callback_query.data.split("|")
    await callback_query.message.edit_text("📥 Downloading video...")

    ydl_opts = {
        'format': format_id,
        'outtmpl': 'downloads/%(title)s.%(ext)s'
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url)
            filename = ydl.prepare_filename(info)
        
        await bot.send_video(callback_query.from_user.id, open(filename, 'rb'))
        await callback_query.message.edit_text("✅ Download complete!")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: {str(e)}")


@dp.callback_query_handler(lambda c: c.data.startswith("mp3|"))
async def callback_mp3(callback_query: types.CallbackQuery):
    _, url = callback_query.data.split("|")
    await callback_query.message.edit_text("🎧 Converting to MP3...")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url)
            filename = ydl.prepare_filename(info).replace(".webm", ".mp3").replace(".m4a", ".mp3")

        await bot.send_audio(callback_query.from_user.id, open(filename, 'rb'))
        await callback_query.message.edit_text("✅ MP3 sent!")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: {str(e)}")


if __name__ == "__main__":
    print("🚀 Bot is running...")
    asyncio.run(dp.start_polling())
