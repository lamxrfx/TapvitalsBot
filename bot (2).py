import os
import logging
import httpx
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ALLOWED_USER_ID   = int(os.environ.get("ALLOWED_USER_ID", "0"))
MODEL             = os.environ.get("MODEL_NAME", "claude-3-haiku-20240307")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

ideas = []
reminders = []
conversation_history = []

SYSTEM_PROMPT = """You are Piv — the sharp, no-nonsense AI business secretary for Lamar Morgan, a UK-based entrepreneur.

Lamar is currently finalising TapVitals: a UK emergency medical ID platform using NFC (Near Field Communication) wristbands (£19.99), cards (£12.99), and bundles (£24.99). It has a free tier, a TapVitals+ subscription, NHS-grade data infrastructure, and a long-term NHS/ambulance trust integration roadmap. The platform is being built in Lovable.

Lamar also runs:
- Prop-firm forex trading on FTMO funded accounts (GBP/JPY and EUR/USD pairs) under Limiqo Ltd
- Pivotal Trading — a trading education platform on Whop
- PSS (Pivotal Swing System) Expert Advisor in MQL4 for MetaTrader 4 — built, awaiting backtesting

Your job:
- Take notes, log ideas, set reminders
- Be a sharp sounding board when asked
- Give concise, direct responses — no fluff, no filler
- Speak like a smart, efficient secretary who knows the business inside out
- Keep responses short on mobile unless detail is asked for
- When you receive a voice note transcription, treat it naturally as if Lamar just said it"""

def ts():
    return datetime.now().strftime("%d %b %Y, %H:%M")

def guard(update):
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return False
    return True

async def ask_claude(user_message):
    conversation_history.append({"role": "user", "content": user_message})
    trimmed = conversation_history[-40:]
    payload = {
        "model": os.environ.get("MODEL_NAME", "claude-3-haiku-20240307"),
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": trimmed,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
            logger.info(f"Anthropic status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
        reply = data["content"][0]["text"]
        conversation_history.append({"role": "assistant", "content": reply})
        return reply
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        return f"❌ API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return f"❌ Error: {str(e)}"

async def start(update, context):
    if not guard(update): return
    await update.message.reply_text(
        "👋 *Piv online.* Your TapVitals secretary is ready.\n\n"
        "/idea `[text]` — log an idea\n"
        "/remind `[30m/2h/14:30]` `[text]` — set a reminder\n"
        "/notes — view all logged ideas\n"
        "/reminders — view upcoming reminders\n"
        "/status — TapVitals build briefing\n"
        "/clear — clear conversation memory\n"
        "/ask `[question]` — ask me anything\n\n"
        "🎤 *Voice notes supported*\n"
        "Or just _talk naturally_ — I'll respond as your secretary.",
        parse_mode="Markdown"
    )

async def idea_cmd(update, context):
    if not guard(update): return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /idea Your idea here")
        return
    ideas.append({"text": text, "timestamp": ts()})
    await update.message.reply_text(f"✅ *Idea logged*\n_{text}_", parse_mode="Markdown")

async def notes_cmd(update, context):
    if not guard(update): return
    if not ideas:
        await update.message.reply_text("No ideas logged yet.")
        return
    msg = "📋 *Your Ideas*\n\n"
    for i, idea in enumerate(ideas, 1):
        msg += f"{i}. _{idea['text']}_\n   `{idea['timestamp']}`\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def remind_cmd(update, context):
    if not guard(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /remind [time] [text]\nFormats: `30m` · `2h` · `14:30`", parse_mode="Markdown")
        return
    time_str = args[0]
    reminder_text = " ".join(args[1:])
    now = datetime.now()
    try:
        if time_str.endswith("m"):
            due = now + timedelta(minutes=int(time_str[:-1]))
        elif time_str.endswith("h"):
            due = now + timedelta(hours=int(time_str[:-1]))
        elif ":" in time_str:
            h, m = time_str.split(":")
            due = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            if due < now:
                due += timedelta(days=1)
        else:
            raise ValueError
    except:
        await update.message.reply_text("❌ Couldn't parse time. Try `30m`, `2h`, or `14:30`.", parse_mode="Markdown")
        return
    delay = (due - now).total_seconds()
    context.job_queue.run_once(fire_reminder, when=delay, chat_id=update.effective_chat.id, data=reminder_text)
    reminders.append({"text": reminder_text, "due": due})
    await update.message.reply_text(f"⏰ *Reminder set*\n_{reminder_text}_\n`{due.strftime('%d %b, %H:%M')}`", parse_mode="Markdown")

async def fire_reminder(context):
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 *REMINDER*\n\n_{context.job.data}_", parse_mode="Markdown")

async def reminders_cmd(update, context):
    if not guard(update): return
    now = datetime.now()
    upcoming = [r for r in reminders if r["due"] > now]
    if not upcoming:
        await update.message.reply_text("No upcoming reminders.")
        return
    msg = "⏰ *Upcoming Reminders*\n\n"
    for r in sorted(upcoming, key=lambda x: x["due"]):
        msg += f"• _{r['text']}_\n  `{r['due'].strftime('%d %b, %H:%M')}`\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    reply = await ask_claude("Give me a sharp TapVitals build status briefing. What's done, what's in progress, what still needs doing before launch. Bullet points. Add a motivational closer.")
    await update.message.reply_text(reply)

async def clear_cmd(update, context):
    if not guard(update): return
    conversation_history.clear()
    await update.message.reply_text("🧹 Memory cleared. Fresh start.")

async def ask_cmd(update, context):
    if not guard(update): return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask What should I prioritise today?")
        return
    await update.message.chat.send_action("typing")
    reply = await ask_claude(question)
    await update.message.reply_text(reply)

async def free_text(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    reply = await ask_claude(update.message.text)
    await update.message.reply_text(reply)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    if not OPENAI_API_KEY:
        await update.message.reply_text("🎤 Add `OPENAI_API_KEY` to Railway variables to enable voice notes.", parse_mode="Markdown")
        return
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        async with httpx.AsyncClient(timeout=30) as client:
            audio_response = await client.get(file.file_path)
            audio_bytes = audio_response.content
        async with httpx.AsyncClient(timeout=60) as client:
            transcribe_response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                data={"model": "whisper-1"},
            )
            transcribe_response.raise_for_status()
            transcript = transcribe_response.json().get("text", "").strip()
        if transcript:
            await update.message.reply_text(f"🎤 _\"{transcript}\"_", parse_mode="Markdown")
            reply = await ask_claude(transcript)
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text("❌ Couldn't transcribe that. Try again or type it out.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Voice note failed. Try typing instead.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("idea",      idea_cmd))
    app.add_handler(CommandHandler("notes",     notes_cmd))
    app.add_handler(CommandHandler("remind",    remind_cmd))
    app.add_handler(CommandHandler("reminders", reminders_cmd))
    app.add_handler(CommandHandler("status",    status_cmd))
    app.add_handler(CommandHandler("clear",     clear_cmd))
    app.add_handler(CommandHandler("ask",       ask_cmd))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    logger.info("Piv is live 🟢")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
