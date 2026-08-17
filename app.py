import os
import asyncio
from flask import Flask, request
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8918221803:AAFQ_nxpm5KalCU4iA4mkUjrBtTMM3zOvBk")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-1004399251962"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LxXlPI1o_VWrogLjwWo1Ym5Ib5JmfV9zjPJl--wOBcw")

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
You are a helpful 24/7 AI assistant for this Telegram group.
Creator Identity: Your developer/creator is "Hridoy Rifat". If anyone asks who created or developed you, state that Hridoy Rifat made you.
Language Rule: 
- If the user sends a message or voice in Bangla (or Banglish/Romanized Bangla), respond in natural Bangla.
- If the user sends a message or voice in English, respond in English.
- Always mirror the language of the incoming message accurately.
"""

tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
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

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    if update.effective_chat.id != GROUP_CHAT_ID:
        return

    file_path = f"temp_voice_{update.message.message_id}.ogg"
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

tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
tg_app.add_handler(MessageHandler(filters.VOICE, handle_voice))

app = Flask(__name__)

# অ্যাপ শুরু হওয়ার সাথে সাথে টেলিগ্রাম বোট ইনিশিয়ালাইজ করার জন্য
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(tg_app.initialize())

@app.route('/')
def home():
    return "Bot is alive!"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        try:
            json_data = request.get_json(force=True)
            update = Update.de_json(json_data, tg_app.bot)
            loop.run_until_complete(tg_app.process_update(update))
            return "ok", 200
        except Exception as e:
            print(f"Error in webhook: {e}")
            return "error", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
