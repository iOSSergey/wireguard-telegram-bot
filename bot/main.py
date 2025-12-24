import os
import re
import logging
from datetime import datetime

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
from bot.storage import get_peer_by_telegram_id


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


# ===== Helpers =====

def safe_filename(name: str) -> str:
    """
    Make filesystem-safe filename from user name.
    """
    name = name.strip()
    name = re.sub(r"[^\w\d_-]+", "_", name, flags=re.UNICODE)
    return name or "wireguard"


# ===== Keyboards =====

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Получить доступ", callback_data="get_access")],
        [InlineKeyboardButton("ℹ️ Проверить доступ", callback_data="check_access")],
    ])


# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    text = (
        "👋 Добро пожаловать!\n\n"
        f"Ваш Telegram ID:\n<code>{user.id}</code>\n\n"
        "Используйте кнопки ниже."
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
    name = user.full_name or user.username or "client"

    try:
        config = get_or_create_peer_and_config(
            telegram_id=user.id,
            name=name,
            ttl_days=30,  # пока фиксировано
        )
    except ProvisionError as e:
        await query.message.reply_text(
            f"❌ Доступ недоступен:\n{e}"
        )
        return

    filename = f"{safe_filename(name)}.conf"

    await query.message.reply_document(
        document=config.encode(),
        filename=filename,
        caption=(
            "✅ Ваш конфигурационный файл WireGuard.\n"
            "Он всегда будет одинаковым."
        ),
    )


async def on_check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    peer = get_peer_by_telegram_id(user.id)

    if not peer:
        await query.message.reply_text(
            "❌ Доступ не найден.\n"
            "Пожалуйста, обратитесь в службу поддержки."
        )
        return

    status = "✅ Активен" if peer["enabled"] else "⛔ Отключён"

    if peer["expires_at"]:
        expires = datetime.fromtimestamp(peer["expires_at"]).strftime("%d.%m.%Y %H:%M")
        expires_text = f"📅 Действует до: {expires}"
    else:
        expires_text = "📅 Срок действия: без ограничения"

    text = (
        "ℹ️ Статус доступа\n\n"
        f"{status}\n"
        f"{expires_text}\n"
        f"🌐 IP: {peer['ip']}"
    )

    await query.message.reply_text(text)


# ===== Entrypoint =====

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_get_access, pattern="^get_access$"))
    app.add_handler(CallbackQueryHandler(on_check_access, pattern="^check_access$"))

    logger.info("Telegram bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

