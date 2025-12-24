import os
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from bot.provision import get_or_create_peer_and_config, ProvisionError


# ===== Logging =====

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ===== Environment =====

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment")


# ===== Keyboards =====

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Получить доступ", callback_data="get_access")]
    ])


# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    text = (
        "👋 Добро пожаловать!\n\n"
        f"Ваш Telegram ID:\n<code>{user.id}</code>\n\n"
        "Нажмите кнопку ниже, чтобы получить доступ."
    )

    await update.message.reply_text(
        text=text,
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


async def on_get_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    try:
        config = get_or_create_peer_and_config(
            telegram_id=user.id,
            name=user.full_name or user.username or "Unknown",
            ttl_days=30,  # можно вынести позже в админку
        )

    except ProvisionError as e:
        await query.message.reply_text(
            f"❌ Доступ недоступен:\n{e}"
        )
        return

    # Отдаём конфигурацию как файл
    filename = f"wg_{user.id}.conf"

    await query.message.reply_document(
        document=config.encode(),
        filename=filename,
        caption="✅ Ваш конфигурационный файл WireGuard.\n"
                "Он всегда будет одинаковым.",
    )


# ===== Entrypoint =====

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_get_access, pattern="^get_access$"))

    logger.info("Telegram bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

