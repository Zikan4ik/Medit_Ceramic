import os
import json
import asyncio
from datetime import datetime

import telegram
from telegram.ext import Application, CommandHandler
from fastapi import FastAPI, Request
from typing import List

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise ValueError("Необхідно задати змінні оточення BOT_TOKEN та CHAT_ID")

HISTORY_FILE = "scan_history.json"
MAX_HISTORY_SIZE = 5

application = Application.builder().token(BOT_TOKEN).build()

def load_history() -> List[dict]:
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_history(history: List[dict]):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

async def latest_scans_command(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID):
        await update.message.reply_text("Ця команда доступна тільки в авторизованому чаті.")
        return

    history = load_history()

    if not history:
        await update.message.reply_text("Історія сканів поки що порожня.")
        return

    message_lines = ["<b>Останні 5 отриманих сканів:</b>\n"]
    for i, scan in enumerate(history, 1):
        scan_time = datetime.fromisoformat(scan['occurredAt'].replace('Z', '+00:00'))
        local_time = scan_time.astimezone(datetime.now().astimezone().tzinfo)
        formatted_date = local_time.strftime('%Y-%m-%d %H:%M:%S')

        message_lines.append(
            f"{i}. <b>{scan['caseName']}</b>\n"
            f"   Пацієнт: {scan['patientName']}\n"
            f"   Час: {formatted_date}\n"
        )

    await update.message.reply_text("\n".join(message_lines), parse_mode='HTML')

app = FastAPI(title="Medit Link Webhook Processor")
application.add_handler(CommandHandler("latest", latest_scans_command))

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    # polling не запускаємо, працюємо через webhook
    print("Telegram Bot запущено.")

@app.on_event("shutdown")
async def shutdown():
    await application.stop()
    await application.shutdown()
    print("Telegram Bot зупинено.")

@app.post("/webhook/medit")
async def medit_webhook(request: Request):
    payload = await request.json()
    print("Отримано:", payload)

    case = payload.get("case")
    if case and "name" in case:
        case_name = case.get("name", "Без назви")
        patient_uuid = case.get("patient", {}).get("uuid", "Немає UUID")
        occurred_at = payload.get("dateIssued")

        # Зберігаємо в історію
        history = load_history()
        history.insert(0, {
            "caseName": case_name,
            "patientName": patient_uuid,
            "occurredAt": occurred_at
        })
        history = history[:MAX_HISTORY_SIZE]
        save_history(history)

        # Надсилаємо в Telegram
        message = (
            f"✅ <b>Новий кейс!</b>\n\n"
            f"<b>Ім'я кейса:</b> {case_name}\n"
            f"<b>Час:</b> {occurred_at}"
        )
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode='HTML'
        )
    else:
        print("Подія не має поля 'case' або 'name'")

    return {"status": "ok"}

@app.get("/")
def root():
    return {"status": "ok", "message": "MeditLink Bot is running"}
