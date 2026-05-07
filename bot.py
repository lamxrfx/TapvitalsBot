import os
import logging
import httpx
import base64
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
MODEL             = "claude-haiku-4-5-20251001"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

ideas = []
reminders = []
conversation_history = []

SYSTEM_PROMPT = """You are Piv — the sharp, no-nonsense AI business secretary for Lamar Morgan, a UK-based entrepreneur.

Lamar is currently finalising TapVitals: a UK emergency medical ID platform using NFC wristbands (19.99), cards (12.99), and bundles (24.99). It has a free tier, a TapVitals+ subscription, NHS-grade data infrastructure, and a long-term NHS/ambulance trust integration roadmap. The platform is being built in Lovable.

Lamar also runs:
- Prop-firm forex trading on FTMO funded accounts under Limiqo Ltd
- Pivotal Trading on Whop
- PSS Expert Advisor in MQL4 for MetaTrader 4

Lamar thinks through MJ DeMarco's CENTS framework: Control, Entry, Need, Time, Scale.

Your job:
- Take notes, log ideas, set reminders
- Draft emails, messages, and content when asked
- Analyse screenshots, emails, documents when images are sent
- Help with decisions, pricing, business analysis
- Be a sharp sounding board when asked
- Give concise, direct responses
- Speak like a smart, efficient secretary who knows the business inside out
- Keep responses short on mobile unless detail is asked for

When drafting, match Lamar's tone: direct, confident, professional but human. No corporate fluff."""

def ts():
    return datetime.now().strftime("%d %b %Y, %H:%M")

def guard(update):
    ALLOWED_IDS = [int(os.environ.get("ALLOWED_USER_ID", "0")), 8678261947]
    if ALLOWED_IDS[0] and update.effective_user.id not in ALLOWED_IDS:
        return False
    return True

async def ask_claude(user_message):
    conversation_history.append({"role": "user", "content": user_message})
    trimmed = conversation_history[-40:]
    payload = {
        "model": MODEL,
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
        return f"API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return f"Error: {str(e)}"

async def ask_claude_with_image(image_bytes, media_type, caption):
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = caption if caption else "Analyse this image and tell me what you see. Be concise and useful."
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": prompt}
            ],
        }
    ]
    payload = {
        "model": MODEL,
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data["content"][0]["text"]
    except Exception as e:
        logger.error(f"Vision error: {e}")
        return f"Error: {str(e)}"

async def start(update, context):
    if not guard(update): return
    await update.message.reply_text(
        "Piv online. Your TapVitals secretary is ready.\n\n"
        "/idea [text] - log an idea\n"
        "/notes - view all logged ideas\n"
        "/remind [30m/2h/14:30] [text] - set a reminder\n"
        "/reminders - view upcoming reminders\n"
        "/draft [type] [brief] - draft an email or message\n"
        "/brain - full briefing of everything\n"
        "/decide [question] - sharp decision breakdown\n"
        "/pivot [idea] - CENTS framework check\n"
        "/weekly - weekly review\n"
        "/price [product] [context] - pricing sanity check\n"
        "/email [paste email] - how to respond\n"
        "/status - TapVitals build briefing\n"
        "/clear - clear conversation memory\n"
        "/ask [question] - ask anything\n\n"
        "Send a photo to analyse it. Or just talk."
    )

async def idea_cmd(update, context):
    if not guard(update): return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /idea Your idea here")
        return
    ideas.append({"text": text, "timestamp": ts()})
    await update.message.reply_text(f"Idea logged: {text}")

async def notes_cmd(update, context):
    if not guard(update): return
    if not ideas:
        await update.message.reply_text("No ideas logged yet.")
        return
    msg = "Your Ideas:\n\n"
    for i, idea in enumerate(ideas, 1):
        msg += f"{i}. {idea['text']} ({idea['timestamp']})\n"
    await update.message.reply_text(msg)

async def remind_cmd(update, context):
    if not guard(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /remind [time] [text]\nFormats: 30m, 2h, 14:30")
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
        await update.message.reply_text("Could not parse time. Try 30m, 2h, or 14:30")
        return
    delay = (due - now).total_seconds()
    context.job_queue.run_once(fire_reminder, when=delay, chat_id=update.effective_chat.id, data=reminder_text)
    reminders.append({"text": reminder_text, "due": due})
    await update.message.reply_text(f"Reminder set: {reminder_text} at {due.strftime('%d %b, %H:%M')}")

async def fire_reminder(context):
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"REMINDER: {context.job.data}")

async def reminders_cmd(update, context):
    if not guard(update): return
    now = datetime.now()
    upcoming = [r for r in reminders if r["due"] > now]
    if not upcoming:
        await update.message.reply_text("No upcoming reminders.")
        return
    msg = "Upcoming Reminders:\n\n"
    for r in sorted(upcoming, key=lambda x: x["due"]):
        msg += f"- {r['text']} at {r['due'].strftime('%d %b, %H:%M')}\n"
    await update.message.reply_text(msg)

async def draft_cmd(update, context):
    if not guard(update): return
    brief = " ".join(context.args)
    if not brief:
        await update.message.reply_text(
            "Usage: /draft [type] [brief]\n\n"
            "Examples:\n"
            "/draft email follow up with NHS contact re TapVitals pilot\n"
            "/draft message chasing NFC supplier on delivery\n"
            "/draft linkedin post announcing TapVitals launch"
        )
        return
    await update.message.chat.send_action("typing")
    prompt = f"Draft the following for Lamar. Be direct, confident and human — no corporate fluff. Just the draft, no preamble:\n\n{brief}"
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def brain_cmd(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    now = datetime.now()
    upcoming = [r for r in reminders if r["due"] > now]
    brain = f"BRAIN DUMP — {ts()}\n\n"
    brain += "CURRENT FOCUS:\n"
    brain += "- Finalising TapVitals on Lovable\n"
    brain += "- NFC wristbands (19.99), cards (12.99), bundles (24.99)\n"
    brain += "- TapVitals+ subscription, NHS integration roadmap\n\n"
    brain += "ALSO RUNNING:\n"
    brain += "- FTMO forex trading under Limiqo Ltd\n"
    brain += "- Pivotal Trading on Whop\n"
    brain += "- PSS Expert Advisor (built, awaiting backtesting)\n\n"
    if ideas:
        brain += f"LOGGED IDEAS ({len(ideas)}):\n"
        for i, idea in enumerate(ideas, 1):
            brain += f"{i}. {idea['text']} ({idea['timestamp']})\n"
        brain += "\n"
    else:
        brain += "LOGGED IDEAS: None yet\n\n"
    if upcoming:
        brain += f"UPCOMING REMINDERS ({len(upcoming)}):\n"
        for r in sorted(upcoming, key=lambda x: x["due"]):
            brain += f"- {r['text']} at {r['due'].strftime('%d %b, %H:%M')}\n"
    else:
        brain += "UPCOMING REMINDERS: None"
    await update.message.reply_text(brain)

async def decide_cmd(update, context):
    if not guard(update): return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /decide [your decision question]\n\nExample: /decide should I launch TapVitals free tier first or paid only")
        return
    await update.message.chat.send_action("typing")
    prompt = f"Lamar needs to make a decision. Give a sharp breakdown: pros, cons, risks, and your clear recommendation. No fluff.\n\nDecision: {question}"
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def pivot_cmd(update, context):
    if not guard(update): return
    idea = " ".join(context.args)
    if not idea:
        await update.message.reply_text("Usage: /pivot [business idea]\n\nExample: /pivot selling NFC medical ID cards to care homes")
        return
    await update.message.chat.send_action("typing")
    prompt = f"""Score this business idea against MJ DeMarco's CENTS framework. Be direct and honest.

Idea: {idea}

Score each: Control, Entry, Need, Time, Scale
Give a verdict: pursue, tweak, or ditch. One paragraph max per dimension."""
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def weekly_cmd(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    now = datetime.now()
    upcoming = [r for r in reminders if r["due"] > now]
    ideas_summary = "\n".join([f"- {i['text']}" for i in ideas[-5:]]) if ideas else "None logged"
    reminders_summary = "\n".join([f"- {r['text']}" for r in upcoming[:5]]) if upcoming else "None"
    prompt = f"""Generate a weekly review for Lamar. Today is {now.strftime('%A %d %B %Y')}.

Recent ideas logged:
{ideas_summary}

Upcoming reminders:
{reminders_summary}

Ask him:
1. What did you ship this week?
2. What blocked you?
3. What is the ONE priority for next week?
4. Are you on track with TapVitals?

Keep it sharp and motivating."""
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def price_cmd(update, context):
    if not guard(update): return
    brief = " ".join(context.args)
    if not brief:
        await update.message.reply_text("Usage: /price [product] [context]\n\nExample: /price TapVitals+ subscription for NHS staff")
        return
    await update.message.chat.send_action("typing")
    prompt = f"""Do a pricing sanity check for Lamar. Consider the market, perceived value, competition, and Lamar's positioning. Be direct.

Product/context: {brief}

Give: recommended price point, reasoning, and any red flags."""
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def email_cmd(update, context):
    if not guard(update): return
    email_text = " ".join(context.args)
    if not email_text:
        await update.message.reply_text("Usage: /email [paste the email you received]\n\nPiv will tell you what they really want and how to respond.")
        return
    await update.message.chat.send_action("typing")
    prompt = f"""Lamar received this email. Tell him:
1. What does this person actually want?
2. What's the right move?
3. Draft a sharp reply.

Email:
{email_text}"""
    reply = await ask_claude(prompt)
    await update.message.reply_text(reply)

async def status_cmd(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    reply = await ask_claude("Give me a sharp TapVitals build status briefing. What's done, what's in progress, what still needs doing before launch. Bullet points. Add a motivational closer.")
    await update.message.reply_text(reply)

async def clear_cmd(update, context):
    if not guard(update): return
    conversation_history.clear()
    await update.message.reply_text("Memory cleared.")

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

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        async with httpx.AsyncClient(timeout=30) as client:
            img_response = await client.get(file.file_path)
            image_bytes = img_response.content
        caption = update.message.caption or ""
        reply = await ask_claude_with_image(image_bytes, "image/jpeg", caption)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("Could not analyse that image. Try again.")

async def voice_handler(update, context):
    if not guard(update): return
    await update.message.chat.send_action("typing")
    if not OPENAI_API_KEY:
        await update.message.reply_text("Add OPENAI_API_KEY to Railway variables to enable voice notes.")
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
            await update.message.reply_text(f'You said: "{transcript}"')
            reply = await ask_claude(transcript)
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text("Could not transcribe. Try again or type instead.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Voice note failed. Try typing instead.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("idea",      idea_cmd))
    app.add_handler(CommandHandler("notes",     notes_cmd))
    app.add_handler(CommandHandler("remind",    remind_cmd))
    app.add_handler(CommandHandler("reminders", reminders_cmd))
    app.add_handler(CommandHandler("draft",     draft_cmd))
    app.add_handler(CommandHandler("brain",     brain_cmd))
    app.add_handler(CommandHandler("decide",    decide_cmd))
    app.add_handler(CommandHandler("pivot",     pivot_cmd))
    app.add_handler(CommandHandler("weekly",    weekly_cmd))
    app.add_handler(CommandHandler("price",     price_cmd))
    app.add_handler(CommandHandler("email",     email_cmd))
    app.add_handler(CommandHandler("status",    status_cmd))
    app.add_handler(CommandHandler("clear",     clear_cmd))
    app.add_handler(CommandHandler("ask",       ask_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    logger.info("Piv is live")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
