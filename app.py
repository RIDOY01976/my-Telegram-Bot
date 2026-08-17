import os
import asyncio
from flask import Flask
from threading import Thread
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# --- ১. Render Web Server (Keep Alive) ---
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- ২. কনফিগারেশন তথ্যসমূহ ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8918221803:AAFQ_nxpm5KalCU4iA4mkUjrBtTMM3zOvBk")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-4399251962"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LxXlPI1o_VWrogLjwWo1Ym5Ib5JmfV9zjPJl--wOBcw")

# --- ৩. Gemini AI Client ও নির্দেশিকা ---
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
You are a helpful 24/7 AI assistant for this Telegram group.
Creator Identity: Your developer/creator is "Hridoy Rifat". If anyone asks who created or developed you, state that Hridoy Rifat made you.
Language Rule: 
- If the user sends a message or voice in Bangla (or Banglish/Romanized Bangla), respond in natural Bangla.
- If the user sends a message or voice in English, respond in English.
- Always mirror the language of the incoming message accurately.
"""

# --- ৪. টেক্সট মেসেজ হ্যান্ডলার ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_CHAT_ID:
        return

    user_text = update.message.text
    if not user_text:
        return

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        print(f"Text Error: {e}")

# --- ৫. ভয়েস মেসেজ হ্যান্ডলার ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_CHAT_ID:
        return

    file_path = "temp_voice.ogg"
    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(file_path)

        uploaded_file = client.files.upload(file=file_path)

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                uploaded_file,
                "Listen to this audio carefully and respond appropriately in the same language."
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        
        await update.message.reply_text(response.text)

    except Exception as e:
        print(f"Voice Error: {e}")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- ৬. বট স্টার্টার ---

