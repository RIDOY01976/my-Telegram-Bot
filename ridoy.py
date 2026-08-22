import os
import io
import asyncio
from collections import defaultdict, deque
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import google.generativeai as genai
from duckduckgo_search import DDGS
from PIL import Image, ImageDraw, ImageFont

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# ==================== FLASK KEEP-ALIVE SERVER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is live and running smooth!", 200

# ==================== ADMIN & MEMORY STATE ====================
IS_PAUSED = False
PAUSE_TIMER_TASK = None

# প্রতি চ্যাটের জন্য আগের ৮টি কথোপকথন মনে রাখার মেমোরি
chat_memories = defaultdict(lambda: deque(maxlen=8))

# ==================== ADVANCED SYSTEM PROMPT ====================
SYSTEM_PROMPT = (
    "You are the most powerful, highly skilled, and friendly AI Assistant for the Telegram group 'Free Rooting Zone💯'. "
    "You were created and are continuously updated by 'হৃদয় ডেভেলপার' (Group Owner). "
    "Your job is to answer ALL types of questions (general knowledge, real-time news, tech, weather, translations, daily chatting, etc.) "
    "with deep expertise in Android Rooting, Bootloader Unlocking, Custom Recoveries (TWRP, OrangeFox), Magisk, KernelSU, APatch, Custom ROMs, and Android customization. "
    "\n\nFormatting & Style Guidelines: "
    "1. Always format responses neatly using bold headers, bullet points, and numbered steps. "
    "2. Use relevant, attractive emojis (like ⚡, 🚀, 💡, 📱, ⚠️, 🛠️, ✨, 🔥, 📌) generously to make the response visually pleasing and engaging. "
    "3. Maintain a polite, respectful, and helpful tone in Bengali or English based on the user's input. "
    "4. For technical or rooting guides, provide clear step-by-step instructions with safety warnings."
)

# ==================== GEMINI HELPER ====================
def generate_gemini_response(chat_id, user_message, image=None, audio_file=None):
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT
    )
    
    contents = []
    
    # ব্যাকগ্রাউন্ড চ্যাট মেমোরি
    if chat_memories[chat_id]:
        history_str = "\n".join([f"{role}: {msg}" for role, msg in chat_memories[chat_id]])
        contents.append(f"[Recent Conversation Memory]:\n{history_str}\n")
    
    if image:
        contents.append(image)
    if audio_file:
        contents.append(audio_file)
        
    contents.append(f"Current User Request: {user_message}")
    
    response = model.generate_content(contents)
    
    # মেমোরিতে নতুন চ্যাট সেভ রাখা
    chat_memories[chat_id].append(("User", user_message))
    if response.text:
        chat_memories[chat_id].append(("Bot", response.text))
        
    return response.text

# ==================== FREE WEB SEARCH ====================
def perform_web_search(query):
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=4):
                results.append(f"Title: {r['title']}\nSnippet: {r['body']}")
        return "\n\n".join(results)
    except Exception as e:
        print(f"Search error: {e}")
        return ""

# ==================== WELCOME IMAGE CARD GENERATOR ====================
async def create_welcome_card(context, user):
    """মেম্বারের প্রোফাইল পিকচার এবং ওয়েলকাম কার্ড ব্যানার তৈরি করে"""
    card_width, card_height = 800, 400
    image = Image.new("RGB", (card_width, card_height), color=(15, 23, 42))
    draw = ImageDraw.Draw(image)
    
    # বর্ডার ও ডেকোরেশন
    draw.rectangle([12, 12, card_width-12, card_height-12], outline=(0, 229, 255), width=4)
    draw.rectangle([20, 20, card_width-20, card_height-20], outline=(59, 130, 246), width=1)
    
    title_text = "WELCOME TO"
    group_text = "Free Rooting Zone 💯"
    name_text = f"{user.full_name}"
    
    # প্রোফাইল পিকচার ডাউনলোড
    user_avatar = None
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            file = await context.bot.get_file(file_id)
            avatar_bytes = await file.download_as_bytearray()
            user_avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            user_avatar = user_avatar.resize((150, 150))
    except Exception as e:
        print(f"Avatar fetch error: {e}")

    # ডিসপ্লে প্রোফাইল পিক
    if user_avatar:
        image.paste(user_avatar, (55, 125))
    else:
        draw.rectangle([55, 125, 205, 275], fill=(30, 41, 59), outline=(0, 229, 255), width=2)

    # টেক্সট বসানো
    draw.text((235, 90), title_text, fill=(255, 255, 255))
    draw.text((235, 130), group_text, fill=(0, 229, 255))
    draw.text((235, 195), f"Member: {name_text}", fill=(250, 204, 21))
    draw.text((235, 255), "Your Hub for Android Rooting & Customization!", fill=(203, 213, 225))
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

# ==================== WELCOME HANDLER ====================
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for new_user in update.message.new_chat_members:
        if new_user.is_bot:
            continue
            
        welcome_img = await create_welcome_card(context, new_user)
        caption = (
            f"👋 **স্বাগতম {new_user.mention_markdown_v2()}\!**\n\n"
            f"আমাদের **Free Rooting Zone💯** পরিবারে আপনাকে স্বাগতম।\n"
            f"এখানে আপনি অ্যান্ড্রয়েড রুট, কাস্টম রম, রিকভারি এবং যেকোনো সাহায্য পাবেন।\n\n"
            f"👑 **Owner & Developer:** HRIDOY"
        )
        
        await update.message.reply_photo(
            photo=welcome_img,
            caption=caption,
            parse_mode="MarkdownV2"
        )

# ==================== ADMIN PAUSE SYSTEM ====================
async def resume_bot(context: ContextTypes.DEFAULT_TYPE):
    global IS_PAUSED
    IS_PAUSED = False

# ==================== MESSAGE HANDLERS ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_PAUSED, PAUSE_TIMER_TASK
    
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # অ্যাডমিন মেসেজ দিলে বোট ২ মিনিটের জন্য পজ হবে
    member = await context.bot.get_chat_member(chat_id, user.id)
    if member.status in ['creator', 'administrator']:
        IS_PAUSED = True
        if PAUSE_TIMER_TASK and not PAUSE_TIMER_TASK.done():
            PAUSE_TIMER_TASK.cancel()
        PAUSE_TIMER_TASK = asyncio.create_task(asyncio.sleep(120))
        PAUSE_TIMER_TASK.add_done_callback(lambda t: asyncio.run_coroutine_threadsafe(resume_bot(context), asyncio.get_event_loop()))
        return

    if IS_PAUSED:
        return

    # ইনপুট মেসেজ ও মিডিয়া ধরা
    message = update.message
    text_input = message.text or message.caption or ""
    image_obj = None
    audio_obj = None

    if message.photo:
        photo_file = await message.photo[-1].get_file()
        img_bytes = await photo_file.download_as_bytearray()
        image_obj = Image.open(io.BytesIO(img_bytes))

    if message.voice or message.audio:
        audio = message.voice or message.audio
        audio_file = await audio.get_file()
        audio_bytes = await audio_file.download_as_bytearray()
        audio_obj = {"mime_type": "audio/ogg", "data": bytes(audio_bytes)}

    # লাইভ ওয়েব সার্চ
    search_context = ""
    if text_input and not image_obj and not audio_obj:
        search_context = perform_web_search(text_input)

    prompt = text_input
    if search_context:
        prompt += f"\n\n[Live Search Context]:\n{search_context}"

    if not prompt and not image_obj and not audio_obj:
        return

    # উত্তর জেনারেট করা
    response_text = generate_gemini_response(chat_id, prompt, image=image_obj, audio_file=audio_obj)
    await message.reply_text(response_text)

# ==================== MAIN BOT LAUNCHER ====================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # ওয়েলকাম মেসেজ হ্যান্ডলার
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    # সাধারণ মেসেজ হ্যান্ডলার
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    # রেন্ডার সার্ভার চালু রাখা
    import threading
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))), daemon=True).start()

    application.run_polling()

if __name__ == '__main__':
    main()
