import os
import re
import logging
import time
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

from bot import storage, wg
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

ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")
if ADMIN_TG_ID and ADMIN_TG_ID.isdigit():
    ADMIN_TG_ID = int(ADMIN_TG_ID)
else:
    ADMIN_TG_ID = None

# only image — no titles/bodies in env
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL")


# ===== Helpers =====

def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\d_-]+", "_", name, flags=re.UNICODE)
    return name or "wireguard"


def is_admin(user_id: int) -> bool:
    return ADMIN_TG_ID is not None and user_id == ADMIN_TG_ID


# ===== Maintenance =====

def restore_peers_on_startup():
    storage.init_db()
    now_ts = int(time.time())
    peers = storage.get_peers_for_restore(now_ts)

    if not peers:
        logger.info("No peers to restore on startup")
        return

    restored = 0
    for peer in peers:
        try:
            wg.enable_peer(peer["public_key"], peer["ip"])
            restored += 1
        except wg.WireGuardError as e:
            logger.error(
                "Failed to enable peer %s (%s): %s",
                peer["public_key"],
                peer["ip"],
                e,
            )

    logger.info("Restored %d peers into WireGuard", restored)


async def expire_peers_job(context: ContextTypes.DEFAULT_TYPE):
    now_ts = int(time.time())
    peers = storage.get_expired_peers(now_ts)
    if not peers:
        return

    for peer in peers:
        try:
            wg.disable_peer(peer["public_key"])
        except wg.WireGuardError as e:
            logger.error(
                "Failed to disable expired peer %s (%s): %s",
                peer["public_key"],
                peer["ip"],
                e,
            )
            continue

        storage.set_enabled(peer["telegram_id"], False)
        logger.info(
            "Peer %s (tg=%s) disabled due to expiry",
            peer["ip"],
            peer["telegram_id"],
        )


# ===== Keyboards =====

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Получить доступ", callback_data="get_access")],
        [InlineKeyboardButton("ℹ️ Проверить доступ", callback_data="check_access")],
    ])


# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Нейтральное приветствие.
    Названия сервиса нет. Картинка — только если указана в .env.
    """

    text = (
        "👋 Добро пожаловать!\n\n"
        "Этот бот поможет вам получить доступ и управлять подключением.\n\n"
        "👉 Нажмите кнопку ниже или отправьте /start ещё раз, чтобы продолжить."
    )

    # если указана картинка — показываем её
    if WELCOME_IMAGE_URL:
        await update.message.reply_photo(
            photo=WELCOME_IMAGE_URL,
            caption=text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            text=text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
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
            ttl_days=30,
        )
    except ProvisionError as e:
        await query.message.reply_text(f"❌ Доступ недоступен:\n{e}")
        return

    filename = f"{safe_filename(name)}.conf"

    await query.message.reply_document(
        document=config.encode(),
        filename=filename,
        caption=(
            "✅ Ваш конфигурационный файл WireGuard.\n"
            "Файл постоянный — сохраняйте его."
        ),
    )


async def on_check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    peer = get_peer_by_telegram_id(user.id)

    if not peer:
        await query.message.reply_text(
            "❌ Доступ не найден. Обратитесь в поддержку."
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


# ===== Admin commands =====

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    text = (
        "🛠 Админ-команды:\n"
        "/admin – справка\n"
        "/user <id> – информация\n"
        "/block <id> – отключить\n"
        "/unblock <id> – включить\n"
        "/extend <id> <days> – продлить"
    )
    await update.message.reply_text(text)


async def admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /user <telegram_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Telegram ID должен быть числом.")
        return

    peer = get_peer_by_telegram_id(target_id)
    if not peer:
        await update.message.reply_text("Пользователь не найден.")
        return

    status = "✅ Активен" if peer["enabled"] else "⛔ Отключён"

    if peer["expires_at"]:
        expires = datetime.fromtimestamp(peer["expires_at"]).strftime("%d.%m.%Y %H:%M")
        expires_text = f"📅 Действует до: {expires}"
    else:
        expires_text = "📅 Срок действия: без ограничения"

    created = datetime.fromtimestamp(peer["created_at"]).strftime("%d.%m.%Y %H:%M")

    text = (
        "ℹ️ Информация\n\n"
        f"👤 ID: <code>{peer['telegram_id']}</code>\n"
        f"Имя: {peer['name']}\n"
        f"{status}\n"
        f"{expires_text}\n"
        f"🌐 IP: {peer['ip']}\n"
        f"📅 Создан: {created}"
    )

    await update.message.reply_text(text, parse_mode="HTML")


async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /block <telegram_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Telegram ID должен быть числом.")
        return

    peer = get_peer_by_telegram_id(target_id)
    if not peer:
        await update.message.reply_text("Пользователь не найден.")
        return

    if not peer["enabled"]:
        await update.message.reply_text("Уже отключён.")
        return

    try:
        wg.disable_peer(peer["public_key"])
    except wg.WireGuardError as e:
        logger.error("Disable error: %s", e)
        await update.message.reply_text("Ошибка при отключении.")
        return

    storage.set_enabled(target_id, False)
    await update.message.reply_text("Пир отключён.")


async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /unblock <telegram_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Telegram ID должен быть числом.")
        return

    peer = get_peer_by_telegram_id(target_id)
    if not peer:
        await update.message.reply_text("Пользователь не найден.")
        return

    now_ts = int(time.time())
    if peer["expires_at"] and peer["expires_at"] <= now_ts:
        await update.message.reply_text(
            "Срок истёк — сначала продлите: /extend <id> <days>"
        )
        return

    if peer["enabled"]:
        await update.message.reply_text("Уже включён.")
        return

    try:
        wg.enable_peer(peer["public_key"], peer["ip"])
    except wg.WireGuardError as e:
        logger.error("Enable error: %s", e)
        await update.message.reply_text("Ошибка при включении.")
        return

    storage.set_enabled(target_id, True)
    await update.message.reply_text("Пир включён.")


async def admin_extend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Использование: /extend <telegram_id> <days>")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID и days должны быть числами.")
        return

    if days <= 0:
        await update.message.reply_text("Days должно быть положительным.")
        return

    peer = get_peer_by_telegram_id(target_id)
    if not peer:
        await update.message.reply_text("Пользователь не найден.")
        return

    now_ts = int(time.time())
    current_exp = peer["expires_at"]

    if current_exp and current_exp > now_ts:
        new_exp = current_exp + days * 24 * 60 * 60
    else:
        new_exp = now_ts + days * 24 * 60 * 60

    storage.update_expiry(target_id, new_exp)

    if not peer["enabled"]:
        try:
            wg.enable_peer(peer["public_key"], peer["ip"])
            storage.set_enabled(target_id, True)
        except wg.WireGuardError:
            pass

    expires_str = datetime.fromtimestamp(new_exp).strftime("%d.%m.%Y %H:%M")
    await update.message.reply_text(f"Новый срок: {expires_str}")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    restore_peers_on_startup()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_get_access, pattern="^get_access$"))
    app.add_handler(CallbackQueryHandler(on_check_access, pattern="^check_access$"))

    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("user", admin_user))
    app.add_handler(CommandHandler("block", admin_block))
    app.add_handler(CommandHandler("unblock", admin_unblock))
    app.add_handler(CommandHandler("extend", admin_extend))

    if app.job_queue:
        app.job_queue.run_repeating(expire_peers_job, interval=60, first=10)

    logger.info("Telegram bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

