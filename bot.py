import logging
import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from yt_dlp import YoutubeDL
import aiofiles

# Enable logging
logging.basicConfig(level=logging.INFO)

# Bot token from Replit secret
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN missing! Add it in Secrets.")
    exit()

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

DOWNLOAD_PATH = "downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# /start command
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 *Welcome!*\nSend me any YouTube, Instagram or Facebook video link.\n"
        "Then choose what you want to download 👇",
        parse_mode="Markdown"
    )

# Handle links
@dp.message_handler(lambda m: any(x in m.text for x in ["youtube.com", "youtu.be", "instagram.com", "facebook.com"]))
async def handle_link(message: types.Message):
    url = message.text.strip()
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🎥 Video", callback_data=f"video|{url}"),
        InlineKeyboardButton("🎵 MP3", callback_data=f"audio|{url}")
    )
    await message.reply("Choose format 👇", reply_markup=keyboard)

# Handle button click
@dp.callback_query_handler(lambda c: "|" in c.data)
async def callback_handler(callback: types.CallbackQuery):
    action, url = callback.data.split("|", 1)
    msg = await callback.message.answer("📥 Downloading... Please wait ⏳")

    ydl_opts = {
        "outtmpl": f"{DOWNLOAD_PATH}/%(title)s.%(ext)s",
    }

    if action == "audio":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        })

    try:
        # ✅ FIXED: create a new event loop instead of using get_event_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        info = await loop.run_in_executor(None, lambda: YoutubeDL(ydl_opts).extract_info(url, download=True))

        filename = YoutubeDL().prepare_filename(info)
        if action == "audio":
            filename = os.path.splitext(filename)[0] + ".mp3"

        async with aiofiles.open(filename, "rb") as f:
            if action == "audio":
                await bot.send_audio(callback.from_user.id, f, caption="🎵 Here's your MP3!")
            else:
                await bot.send_video(callback.from_user.id, f, caption="🎬 Here's your video!")

        await msg.edit_text("✅ Done!")
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

# Start bot
if __name__ == "__main__":
    print("🚀 Bot is running...")
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
