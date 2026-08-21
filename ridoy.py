import datetime
import io
import os
from threading import Thread

from flask import Flask, jsonify
import google.genai as genai
from google.genai import types
from PIL import Image
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
GEMINI_MODEL = "gemini-2.5-flash"  # গুগল সার্চ টুল সাপোর্টের জন্য ২.৫ ফ্ল্যাশ মডেল ব্যবহার করা হয়েছে
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

    # বর্তমান তারিখ ও সময় সংগ্রহ (বাংলাদেশ টাইমজোনের জন্য UTC+6 যোগ করা হয়েছে)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    bd_time = now_utc + datetime.timedelta(hours=6)
    current_datetime_str = bd_time.strftime("%A, %B %d, %Y - %I:%M:%S %p (Asia/Dhaka)")

    system_prompt = (
        "You are an intelligent, highly accurate, helpful, and friendly Telegram group assistant. "
        f"CURRENT LOCAL DATE AND TIME: {current_datetime_str}. "
        "STRICT INSTRUCTION FOR FACTUAL ACCURACY: You must strictly provide accurate, verified, and factual answers based on real-time awareness. "
        "Use Google Search whenever necessary to search for real-time information, latest phone models, custom recoveries (TWRP, OrangeFox), ROM files, kernels, software updates, or technical specifications. "
        "DO NOT make up facts, hallucinate, or guess. If you do not find verified information or if you are unsure, politely state that you do not have that specific information. "
        "Detect the language of the user's message. If the user asks in Bengali, "
        "reply naturally in Bengali. If the user asks in English, reply in English. "
        "Always maintain a polite, human-like, conversational tone. "
        "If an image is attached, analyze the image along with any accompanying text "
        "to solve their problem or answer their query accurately. If an audio message is attached, "
        "listen to it, transcribe it, understand its meaning, and respond helpfully. "
        "If the user asks who created or developed you, including phrases such as "
        '"তোমাকে কে তৈরি করেছে", "who made you", or "tomake ke toiri korse", '
        "always respond that you were created by Hridoy Developer (হৃদয় ডেভেলপার), "
        "who is also the owner of this group (এই গ্রুপের অনার), and that he "
        "constantly and regularly updates you (তিনিই আমাকে প্রতিনিয়ত আপডেট করেন)."
    )

    # জেমিনি কনফিগারেশন - গুগল সার্চ টুল ও কড়া সিস্টেম ইনস্ট্রাকশন
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )

    try:
        if update.message.photo:
            caption = update.message.caption or "Please analyze this image."
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()

            image = Image.open(io.BytesIO(photo_bytes))
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[caption, image],
                config=config,
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
                    audio_part,
                    "Listen to this audio, transcribe it, understand it, and respond to the speaker.",
                ],
                config=config,
            )
            await update.message.reply_text(response.text)

        elif update.message.text:
            text = update.message.text
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=text,
                config=config,
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
