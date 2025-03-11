from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context):
    await update.message.reply_text("Hej! Jag är din ToughLove Life Coach! 💪")

async def handle_message(update: Update, context):
    text = update.message.text
    await update.message.reply_text(f"Du sa: {text} – Vad kan jag hjälpa dig med idag?")

def start_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app.run_polling()
