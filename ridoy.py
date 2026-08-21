import datetime
import io
import os
from threading import Thread

from flask import Flask, jsonify
import google.genai as genai
from google.genai import types
from PIL import Image

# Timezone-এর জন্য zoneinfo ব্যবহার করা হয়েছে
try:
    import zoneinfo
    BD_TZ = zoneinfo.ZoneInfo("Asia/Dhaka")
except Exception:
    BD_TZ = datetime.timezone(datetime.timedelta(hours=6))

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not configured")

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.1-flash-lite"
ALLOWED_CHAT_ID = -1004399251962

# Render Environment Variable থেকে PORT নেবে, না পেলে ১০০০০ ব্যবহার করবে
HEALTH_PORT = int(os.environ.get("PORT", 10000))

web_app = Flask(__name__)


@web_app.get("/")
def home():
    return "Bot is alive and running!", 200


@web_app.get("/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "service": "python-explorer-telegram-bot",
        }
    )


def run():
    web_app.run(
        host="0.0.0.0",
        port=HEALTH_PORT,
        debug=False,
        use_reloader=False,
    )


def keep_alive():
    Thread(
        target=run,
        name="health-server",
        daemon=True,
    ).start()


last_admin_activity = None
PAUSE_DURATION_MINUTES = 2


def is_bot_paused() -> bool:
    global last_admin_activity
    if last_admin_activity is None:
        return False

    elapsed = datetime.datetime.now() - last_admin_activity
    if elapsed < datetime.timedelta(minutes=PAUSE_DURATION_MINUTES):
        return True

    last_admin_activity = None
    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_admin_activity

    if not update.message or not update.effective_chat:
        return

    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    if user is None:
        return

    chat_member = await context.bot.get_chat_member(chat_id, user.id)
    is_admin = chat_member.status in ["administrator", "creator"]

    mentions_admin = False
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type in ["mention", "text_mention"]:
                mentions_admin = True
                break

    if is_admin or mentions_admin:
        last_admin_activity = datetime.datetime.now()
        return

    if is_bot_paused():
        return

    # বর্তমান বাংলাদেশ সময় নির্ধারণ
    current_time_str = datetime.datetime.now(BD_TZ).strftime("%Y-%m-%d %I:%M:%S %p %Z")

    system_prompt = (
        "You are an intelligent, helpful, and friendly Telegram group assistant.\n"
        f"Current Bangladesh Time (Asia/Dhaka): {current_time_str}\n\n"
        "INSTRUCTIONS FOR ACCURACY AND GOOGLE SEARCH:\n"
        "1. Always perform a Google Search before answering factual questions, mobile phone models, "
        "custom ROMs, kernel specs, hardware information, news, or current events. Do NOT guess or hallucinate.\n"
        "2. Ensure all provided information is 100% accurate and up-to-date. If you are unsure or information "
        "is not available after searching, clearly state that you don't know rather than giving wrong information.\n"
        "3. Detect the language of the user's message. If the user asks in Bengali, reply naturally in Bengali. "
        "If the user asks in English, reply in English.\n"
        "4. Always maintain a polite, human-like, conversational tone.\n"
        "5. If an image is attached, analyze the image along with any accompanying text to solve their problem.\n"
        "6. If an audio message is attached, listen to it, transcribe it, understand its meaning, and respond helpfully.\n"
        "7. If the user asks who created or developed you, including phrases such as "
        '"তোমাকে কে তৈরি করেছে", "who made you", or "tomake ke toiri korse", '
        "always respond that you were created by Hridoy Developer (হৃদয় ডেভেলপার), "
        "who is also the owner of this group (এই গ্রুপের অনার), and that he "
        "constantly and regularly updates you (তিনিই আমাকে প্রতিনিয়ত আপডেট করেন)."
    )

    # Google Search Tool সেটআপ
    search_config = types.GenerateContentConfig(
        tools=[{"google_search": {}}]
    )

    try:
        if update.message.photo:
            caption = update.message.caption or "Please analyze this image."
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()

            image = Image.open(io.BytesIO(photo_bytes))
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[system_prompt, caption, image],
                config=search_config,
            )
            await update.message.reply_text(response.text)

        elif update.message.voice or update.message.audio:
            telegram_audio = update.message.voice or update.message.audio
            audio_file = await context.bot.get_file(telegram_audio.file_id)
            audio_bytes = await audio_file.download_as_bytearray()

            default_mime_type = (
                "audio/ogg" if update.message.voice else "audio/mpeg"
            )
            mime_type = getattr(telegram_audio, "mime_type", None) or default_mime_type
            audio_part = types.Part.from_bytes(
                data=bytes(audio_bytes),
                mime_type=mime_type,
            )
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    system_prompt,
                    audio_part,
                    "Listen to this audio, transcribe it, understand it, and respond to the speaker.",
                ],
                config=search_config,
            )
            await update.message.reply_text(response.text)

        elif update.message.text:
            text = update.message.text
            prompt = f"{system_prompt}\n\nUser message: {text}"
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=search_config,
            )
            await update.message.reply_text(response.text)

    except Exception as error:
        print(f"Error processing message: {error}")


if __name__ == "__main__":
    keep_alive()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO,
            handle_message,
        )
    )
    print(f"Health server listening on port {HEALTH_PORT}", flush=True)
    print("Bot is running...", flush=True)
    app.run_polling()
