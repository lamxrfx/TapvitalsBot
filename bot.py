import os
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID  = int(os.environ.get("ALLOWED_USER_ID", "0"))  # Your Telegram user ID

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── In-memory store ───────────────────────────────────────────────────────────
ideas    = []          # list of {text, timestamp}
reminders = []         # list of {text, due: datetime, job_name}
conversation_history = []   # Claude multi-turn memory

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
- When Lamar is thinking through something, push back constructively if needed
- Keep responses short on mobile unless detail is asked for

You are NOT a generic chatbot. You know Lamar's businesses, his goals, and his time is valuable."""


# ── Helpers ───────────────────────────────────────────────────────────────────
def ts() -> str:
    return datetime.now().strftime("%d %b %Y, %H:%M")

def guard(update: Update) -> bool:
    """Return False and warn if message is from an unauthorised user."""
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return False
    return True

async def ask_claude(user_message: str) -> str:
    """Send a message to Claude with full conversation history."""
    conversation_history.append({"role": "user", "content": user_message})
    # Keep last 40 turns to stay within token limits
    trimmed = conversation_history[-40:]
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=trimmed
    )
    reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": reply})
    return reply


# ── Command Handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    await update.message.reply_text(
        "👋 *Piv online.* Your TapVitals secretary is ready.\n\n"
        "Commands:\n"
        "/idea `[text]` — log an idea\n"
        "/remind `[Xm/Xh/HH:MM]` `[text]` — set a reminder\n"
        "/notes — view all logged ideas\n"
        "/reminders — view upcoming reminders\n"
        "/status — TapVitals build status briefing\n"
        "/clear — clear conversation memory\n"
        "/ask `[question]` — ask me anything\n\n"
        "Or just _talk_ — I'll respond as your secretary.",
        parse_mode="Markdown"
    )

async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /idea Your idea here")
        return
    ideas.append({"text": text, "timestamp": ts()})
    await update.message.reply_text(f"✅ *Idea logged* — {ts()}\n_{text}_", parse_mode="Markdown")

async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    if not ideas:
        await update.message.reply_text("No ideas logged yet. Use /idea to add one.")
        return
    msg = "📋 *Your Ideas*\n\n"
    for i, idea in enumerate(ideas, 1):
        msg += f"{i}. _{idea['text']}_\n   `{idea['timestamp']}`\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /remind [time] [text]\n\n"
            "Time formats:\n"
            "• `30m` — in 30 minutes\n"
            "• `2h` — in 2 hours\n"
            "• `14:30` — at 2:30 PM today",
            parse_mode="Markdown"
        )
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
            raise ValueError("Unknown format")
    except Exception:
        await update.message.reply_text("❌ Couldn't parse that time. Try `30m`, `2h`, or `14:30`.", parse_mode="Markdown")
        return

    delay = (due - now).total_seconds()
    job_name = f"reminder_{len(reminders)}"

    context.job_queue.run_once(
        fire_reminder,
        when=delay,
        chat_id=update.effective_chat.id,
        name=job_name,
        data=reminder_text
    )

    reminders.append({"text": reminder_text, "due": due, "job_name": job_name})
    await update.message.reply_text(
        f"⏰ *Reminder set*\n_{reminder_text}_\n`{due.strftime('%d %b, %H:%M')}`",
        parse_mode="Markdown"
    )

async def fire_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Called by job queue when reminder is due."""
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"🔔 *REMINDER*\n\n_{context.job.data}_",
        parse_mode="Markdown"
    )

async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    now = datetime.now()
    upcoming = [r for r in reminders if r["due"] > now]
    if not upcoming:
        await update.message.reply_text("No upcoming reminders. Use /remind to add one.")
        return
    msg = "⏰ *Upcoming Reminders*\n\n"
    for r in sorted(upcoming, key=lambda x: x["due"]):
        msg += f"• _{r['text']}_\n  `{r['due'].strftime('%d %b, %H:%M')}`\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    prompt = (
        "Give me a sharp TapVitals build status briefing based on what you know. "
        "List: what's likely done, what's likely in progress, what still needs doing before launch. "
        "Be concise — bullet points. Add a motivational closer."
    )
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    conversation_history.clear()
    await update.message.reply_text("🧹 Conversation memory cleared. Fresh start.")

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask What should I prioritise today?")
        return
    await update.message.chat.send_action("typing")
    reply = await ask_claude(question)
    await update.message.reply_text(reply)

async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any plain message as a natural conversation."""
    if not guard(update): return
    user_msg = update.message.text
    await update.message.chat.send_action("typing")
    reply = await ask_claude(user_msg)
    await update.message.reply_text(reply)


# ── Main ──────────────────────────────────────────────────────────────────────
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    logger.info("Piv is live 🟢")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
