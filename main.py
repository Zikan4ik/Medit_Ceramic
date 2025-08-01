import os
import json
import asyncio
import logging
from datetime import datetime, timezone

import telegram
from telegram.ext import Application, CommandHandler
from fastapi import FastAPI, Request, HTTPException
from typing import List, Optional

# --- Налаштування логування ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Змінні оточення ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
# CHAT_ID можна використовувати як адмінський ID або ID чату для повідомлень
ADMIN_CHAT_ID = os.getenv("CHAT_ID") # Змінив назву, щоб було зрозуміліше

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    logger.error("Необхідно задати змінні оточення BOT_TOKEN та CHAT_ID")
    # Краще не викидати ValueError, а завершити програму або повідомити користувача
    exit("Помилка конфігурації: Переконайтеся, що BOT_TOKEN та CHAT_ID встановлені.")

# --- Файл історії та ліміт ---
HISTORY_FILE = "scan_history.json"
MAX_HISTORY_SIZE = 5

# --- Функції для роботи з історією ---
def load_history() -> List[dict]:
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except (json.JSONDecodeError) as e:
        logger.error(f"Помилка декодування JSON файлу історії: {e}")
        return []

def save_history(history: List[dict]):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Помилка запису файлу історії: {e}")

# --- Ініціалізація Telegram Bot Application ---
application = Application.builder().token(BOT_TOKEN).build()
# Глобальний об'єкт bot, який можна використовувати в FastAPI ендпоінтах
telegram_bot = application.bot

# --- Command Handler ---
async def latest_scans_command(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("Ця команда доступна тільки в авторизованому чаті.")
        return

    history = load_history()

    if not history:
        await update.message.reply_text("Історія сканів поки що порожня.")
        return

    message_lines = ["<b>Останні 5 отриманих сканів:</b>\n"]
    for i, scan in enumerate(history, 1):
        scan_time_str = scan.get('occurredAt')
        case_name = scan.get('caseName', 'Невідомий кейс')
        patient_name = scan.get('patientName', 'Невідомий пацієнт')
        
        formatted_date = "Невідомий час"
        if scan_time_str:
            try:
                # Враховуємо 'Z' як UTC і перетворюємо в локальний час
                scan_time = datetime.fromisoformat(scan_time_str.replace('Z', '+00:00'))
                local_time = scan_time.astimezone(datetime.now(timezone.utc).astimezone().tzinfo)
                formatted_date = local_time.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                logger.warning(f"Невірний формат часу в історії: {scan_time_str}")

        message_lines.append(
            f"{i}. <b>{case_name}</b>\n"
            f"   Пацієнт: {patient_name}\n"
            f"   Час: {formatted_date}\n"
        )

    await update.message.reply_text("\n".join(message_lines), parse_mode='HTML')

application.add_handler(CommandHandler("latest", latest_scans_command))

# --- Ініціалізація FastAPI ---
app = FastAPI(title="Medit Link Webhook Processor")

@app.on_event("startup")
async def startup_event():
    # Ініціалізуємо та запускаємо Telegram Application
    await application.initialize()
    await application.start()
    logger.info("Telegram Bot запущено.")

@app.on_event("shutdown")
async def shutdown_event():
    # Зупиняємо Telegram Application
    await application.stop()
    await application.shutdown()
    logger.info("Telegram Bot зупинено.")

# --- Webhook Endpoint ---
@app.post("/webhook/medit")
async def handle_medit_webhook(request: Request):
    try:
        event = await request.json()
        logger.info(f"Отримано вебхук Medit: {json.dumps(event, ensure_ascii=False)}") # Логуємо для дебагу

        case_name: Optional[str] = None
        patient_name: Optional[str] = None
        occurred_at: Optional[str] = datetime.now(timezone.utc).isoformat(timespec='milliseconds') + 'Z' # Час отримання вебхука за замовчуванням

        # --- Логіка обробки різних типів подій ---
        # Варіант 1: Подія з 'case' на верхньому рівні
        if 'case' in event and isinstance(event['case'], dict):
            case_data = event['case']
            case_name = case_data.get('name')
            
            # Якщо є пацієнт, спробуємо отримати його ім'я
            patient_data = case_data.get('patient')
            if isinstance(patient_data, dict):
                patient_name = patient_data.get('name') # Можливо, 'name' в patient
            
            # Для case подій, час створення або сканування може бути у 'dateCreated'/'dateScanned'
            if case_data.get('dateScanned'):
                occurred_at = case_data['dateScanned']
            elif case_data.get('dateCreated'):
                occurred_at = case_data['dateCreated']

            message_info = f"Новий кейс: **{case_name if case_name else 'Невідомий'}**\n"
            message_info += f"Пацієнт: {patient_name if patient_name else 'Невідомий'}\n"
            message_info += f"Статус: {case_data.get('status', 'Невідомий')}\n"
            message_info += f"Час: {datetime.fromisoformat(occurred_at.replace('Z', '+00:00')).astimezone(datetime.now(timezone.utc).astimezone().tzinfo).strftime('%Y-%m-%d %H:%M:%S')}"
            
        # Варіант 2: Подія з 'order', де 'case' вкладений
        elif 'order' in event and isinstance(event['order'], dict):
            order_data = event['order']
            
            # Спроба отримати case_name та patient_name з вкладеного 'case'
            if 'case' in order_data and isinstance(order_data['case'], dict):
                case_data_in_order = order_data['case']
                case_name = case_data_in_order.get('name')
                
                patient_data = case_data_in_order.get('patient')
                if isinstance(patient_data, dict):
                    patient_name = patient_data.get('name') # Можливо, 'name' в patient

            order_number = order_data.get('orderNumber', 'N/A')
            seller_name = order_data.get('seller', {}).get('name', 'N/A')
            
            # Для order подій, час може бути 'dateCreated'
            if order_data.get('dateCreated'):
                occurred_at = order_data['dateCreated']

            message_info = f"Нове замовлення №`{order_number}`\n"
            message_info += f"Від: `{seller_name}`\n"
            message_info += f"Кейс: **{case_name if case_name else 'Невідомий'}**\n"
            message_info += f"Пацієнт: {patient_name if patient_name else 'Невідомий'}\n"
            message_info += f"Статус замовлення: {order_data.get('status', 'Невідомий')}\n"
            message_info += f"Час: {datetime.fromisoformat(occurred_at.replace('Z', '+00:00')).astimezone(datetime.now(timezone.utc).astimezone().tzinfo).strftime('%Y-%m-%d %H:%M:%S')}"

        else:
            # Якщо формат не розпізнано
            logger.warning(f"Отримано нерозпізнаний вебхук Medit: {json.dumps(event, ensure_ascii=False)}")
            await telegram_bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ **Увага:** Отримано нерозпізнану подію Medit.\n\n`{json.dumps(event, indent=2, ensure_ascii=False)}`",
                parse_mode='Markdown'
            )
            return {"status": "error", "message": "Unrecognized event format"}, 400

        # --- Збереження історії ---
        if case_name: # Зберігаємо в історію тільки якщо вдалося отримати case_name
            history = load_history()
            history.insert(0, {
                'caseName': case_name,
                'patientName': patient_name,
                'occurredAt': occurred_at
            })
            if len(history) > MAX_HISTORY_SIZE:
                history = history[:MAX_HISTORY_SIZE]
            save_history(history)

            # --- Відправка повідомлення в Telegram ---
            await telegram_bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"✅ {message_info}",
                parse_mode='Markdown'
            )
        else:
            logger.warning("Не вдалося отримати case_name для збереження в історію.")


        return {"status": "success", "message": "Webhook processed"}

    except Exception as e:
        logger.error(f"Помилка при обробці вебхука Medit: {e}", exc_info=True)
        # Відправка повідомлення про помилку в Telegram
        if ADMIN_CHAT_ID:
            await telegram_bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ **Помилка при обробці вебхука Medit:**\n`{e}`\n\n**Подія:**\n`{json.dumps(await request.json(), indent=2, ensure_ascii=False) if await request.body() else 'N/A'}`",
                parse_mode='Markdown'
            )
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

# --- Root Endpoint ---
@app.get("/")
def root():
    return {"status": "ok", "message": "MeditLink Bot is running"}
