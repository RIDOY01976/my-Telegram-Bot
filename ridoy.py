from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import time
from typing import Any

from aiohttp import web
from google import genai
from google.genai import types
from google.genai.errors import APIError
from telegram import Message, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError


# Telegram returns supergroup IDs with the -100 prefix. The user-provided
# numeric suffix resolves to this exact group.
GROUP_ID = -1004399251962
CREATOR_NAME = "Hriday Developer"
ADMIN_SILENCE_SECONDS = 120
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_REPLY_LENGTH = 3900
WEBHOOK_PATH = os.getenv("TELEGRAM_WEBHOOK_PATH", "/api/telegram/webhook")
HEALTH_PATH = "/api/healthz"

logger = logging.getLogger("hriday-telegram-bot")


class AdminSilence:
    """Tracks the rolling quiet period after an admin message."""

    def __init__(self, duration_seconds: int) -> None:
        self.duration_seconds = duration_seconds
        self._silent_until = 0.0

    def reset(self) -> None:
        self._silent_until = time.monotonic() + self.duration_seconds

    def is_active(self) -> bool:
        return time.monotonic() < self._silent_until


class GroupBot:
    def __init__(self, genai_client: genai.Client) -> None:
        self.ai = genai_client
        self.admin_silence = AdminSilence(ADMIN_SILENCE_SECONDS)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if (
            message is None
            or chat is None
            or user is None
            or chat.id != GROUP_ID
            or chat.type not in {"group", "supergroup"}
            or user.is_bot
        ):
            return

        admin_status = await self._is_admin(context, user.id)
        if admin_status is None:
            # If the bot cannot verify membership, fail closed rather than
            # accidentally replying during an admin-only silence period.
            return
        if admin_status:
            self.admin_silence.reset()
            return
        if self.admin_silence.is_active():
            return

        try:
            response = await self._process_message(message, context)
            if response:
                await self._reply_in_chunks(message, response)
        except APIError:
            logger.warning("Gemini API usage limit or request error reached")
            await message.reply_text(self._ai_limit_message(message))
        except Exception as exc:
            logger.error("Message processing failed (%s)", type(exc).__name__)
            await message.reply_text(self._error_message(message))

    async def _is_admin(
        self, context: ContextTypes.DEFAULT_TYPE, user_id: int
    ) -> bool | None:
        try:
            member = await context.bot.get_chat_member(GROUP_ID, user_id)
        except TelegramError as exc:
            logger.warning(
                "Could not verify admin status for user %s (%s)",
                user_id,
                type(exc).__name__,
            )
            return None
        return member.status in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            "creator",
            "administrator",
        }

    async def _process_message(
        self, message: Message, context: ContextTypes.DEFAULT_TYPE
    ) -> str | None:
        text = (message.text or message.caption or "").strip()
        if self._is_creator_question(text):
            return self._creator_reply(text)

        if message.voice or message.audio:
            return await self._voice_reply(message, context)

        if message.photo:
            return await self._image_reply(message, context)

        if not text:
            return None

        if text.startswith("/"):
            command = text.split(maxsplit=1)[0].lower().split("@", maxsplit=1)[0]
            if command in {"/creator", "/developer", "/about"}:
                return self._creator_reply(text)

        return await self._text_reply(text)

    async def _text_reply(self, text: str) -> str:
        system_instruction = (
            "You are a helpful Telegram group assistant. "
            "Reply in Bengali when the user's message is Bengali; "
            "otherwise reply in English. Keep replies concise and "
            "friendly for a group chat. Do not claim to be human, "
            f"and identify the creator as {CREATOR_NAME} if asked."
        )
        response = await self.ai.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=600,
            ),
        )
        return (response.text or "").strip()

    async def _voice_reply(
        self, message: Message, context: ContextTypes.DEFAULT_TYPE
    ) -> str:
        voice = message.voice
        audio = message.audio
        telegram_file = await context.bot.get_file(
            voice.file_id if voice else audio.file_id  # type: ignore[union-attr]
        )
        audio_bytes = bytes(await telegram_file.download_as_bytearray())
        mime_type = (
            (voice.mime_type if voice else None)
            or (audio.mime_type if audio else None)
            or "audio/ogg"
        )
        
        system_instruction = (
            "You are a helpful Telegram group assistant. Transcribe and answer the "
            "audio message accurately. Reply in Bengali if the audio is Bengali, "
            f"otherwise in English. Identify creator as {CREATOR_NAME} if asked."
        )

        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        response = await self.ai.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=["Please process this audio message and respond appropriately.", audio_part],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=700,
            ),
        )
        answer = (response.text or "").strip()
        if not answer:
            return self._localized_message(
                message, "দুঃখিত, অডিওটি বুঝতে পারিনি।", "Sorry, I could not understand the audio."
            )

        return answer

    async def _image_reply(
        self, message: Message, context: ContextTypes.DEFAULT_TYPE
    ) -> str:
        photo = message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        caption = (message.caption or "").strip()
        language_hint = (
            "Answer in Bengali."
            if self._is_bengali(caption)
            else "Answer in English unless the image or caption clearly calls for Bengali."
        )
        prompt = (
            "Analyze the attached image for the group member. Describe what is "
            "useful or relevant, avoid inventing details, and keep the reply concise. "
            f"{language_hint}"
        )
        if caption:
            prompt += f"\nThe member's question or caption is: {caption}"

        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        response = await self.ai.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a careful image-recognition assistant in a Telegram "
                    f"group. The bot creator is {CREATOR_NAME}."
                ),
                max_output_tokens=700,
            ),
        )
        return (response.text or "").strip()

    @staticmethod
    async def _reply_in_chunks(message: Message, response: str) -> None:
        cleaned = response.strip()
        for start in range(0, len(cleaned), MAX_REPLY_LENGTH):
            await message.reply_text(
                cleaned[start : start + MAX_REPLY_LENGTH],
                disable_web_page_preview=True,
            )

    @staticmethod
    def _is_bengali(text: str) -> bool:
        return any("\u0980" <= character <= "\u09ff" for character in text)

    @classmethod
    def _is_creator_question(cls, text: str) -> bool:
        if not text:
            return False
        normalized = text.lower()
        english_patterns = (
            r"\b(creator|developer|author)\b",
            r"\b(who\s+(made|created|built)\s+(you|this|the bot))\b",
            r"\bwho\s+is\s+behind\s+(you|this|the bot)\b",
        )
        bengali_phrases = (
            "নির্মাতা",
            "ডেভেলপার",
            "কে বানিয়েছে",
            "কে তৈরি করেছে",
            "কার তৈরি",
        )
        return any(re.search(pattern, normalized) for pattern in english_patterns) or any(
            phrase in text for phrase in bengali_phrases
        )

    @classmethod
    def _creator_reply(cls, text: str) -> str:
        if cls._is_bengali(text):
            return f"এই বটটি তৈরি ও আপডেট করেছেন {CREATOR_NAME}, যিনি এই গ্রুপের মালিক।"
        return f"This bot was created and updated by {CREATOR_NAME}, the owner of this group."

    @classmethod
    def _localized_message(cls, message: Message, bengali: str, english: str) -> str:
        source = message.text or message.caption or ""
        return bengali if cls._is_bengali(source) else english

    @classmethod
    def _error_message(cls, message: Message) -> str:
        return cls._localized_message(
            message,
            "দুঃখিত, এখন উত্তর দিতে সমস্যা হচ্ছে। একটু পরে আবার চেষ্টা করুন।",
            "Sorry, I could not process that right now. Please try again in a moment.",
        )

    @classmethod
    def _ai_limit_message(cls, message: Message) -> str:
        return cls._localized_message(
            message,
            "AI সেবার ব্যবহারসীমা শেষ হয়ে গেছে। একটু পরে আবার চেষ্টা করুন অথবা সক্রিয় Gemini API Key চেক করুন।",
            "The AI service has reached its limit. Please try again in a moment or verify your Gemini API key.",
        )


def build_application() -> Application:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not configured.")

    bot = GroupBot(genai.Client(api_key=gemini_key))
    application = (
        Application.builder()
        .token(telegram_token)
        .concurrent_updates(False)
        .build()
    )
    application.add_handler(MessageHandler(filters.ALL, bot.handle_message))
    return application


async def run_webhook(application: Application) -> None:
    """Serve Telegram updates over HTTP on the deployment's public port."""
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
    port = int(os.getenv("PORT", "8080"))

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "telegram-bot"})

    async def telegram_webhook(request: web.Request) -> web.Response:
        if webhook_secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != webhook_secret:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=403)
        try:
            payload = await request.json()
            update = Update.de_json(payload, application.bot)
            await application.process_update(update)
        except (ValueError, TypeError):
            logger.warning("Invalid Telegram webhook payload")
            return web.json_response({"ok": False, "error": "invalid payload"}, status=400)
        except Exception as exc:
            logger.error(
                "Telegram webhook update processing failed (%s)",
                type(exc).__name__,
            )
            return web.json_response({"ok": False, "error": "processing failed"}, status=500)
        return web.json_response({"ok": True})

    server = web.Application()
    server.router.add_get(HEALTH_PATH, health)
    server.router.add_post(WEBHOOK_PATH, telegram_webhook)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)

    await application.initialize()
    await application.start()
    await site.start()
    logger.info("Webhook server listening on port %s at %s", port, WEBHOOK_PATH)

    try:
        if webhook_url:
            await application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
                secret_token=webhook_secret,
                drop_pending_updates=True,
            )
            logger.info("Telegram webhook registered.")
        else:
            logger.warning(
                "TELEGRAM_WEBHOOK_URL is not set; HTTP server is ready but "
                "Telegram webhook registration was skipped."
            )
        await asyncio.Event().wait()
    finally:
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hriday Developer Telegram bot")
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="serve Telegram updates over HTTP instead of polling",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Telegram's HTTP URLs contain the bot token. Never emit request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    logger.info("Starting group-only Telegram bot for configured group.")
    application = build_application()
    if args.webhook:
        asyncio.run(run_webhook(application))
    else:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
