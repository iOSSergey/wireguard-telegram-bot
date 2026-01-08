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

BOT_NAME = os.getenv("BOT_NAME", "VPN Bot")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL")
SUPPORT_TG_USERNAME = os.getenv("SUPPORT_TG_USERNAME")
INSTALL_GUIDE_URL = os.getenv("INSTALL_GUIDE_URL")

MAX_DEVICES_PER_USER = int(os.getenv("MAX_DEVICES_PER_USER", "1"))


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

def main_keyboard(user_id: int | None = None):
    buttons = [
        [InlineKeyboardButton("🔐 Получить VPN", callback_data="get_access")],
        [
            InlineKeyboardButton("ℹ️ Мой доступ", callback_data="check_access"),
            InlineKeyboardButton("📡 Как установить", callback_data="how_install"),
        ],
        [
            InlineKeyboardButton("🤝 Поддержка", callback_data="support"),
            InlineKeyboardButton("🎟 Ввести промокод", callback_data="promo"),
        ],
    ]

    if user_id and is_admin(user_id):
        buttons.append(
            [InlineKeyboardButton("🛠 Администрирование", callback_data="admin_panel")]
        )

    return InlineKeyboardMarkup(buttons)


# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"👋 Привет!\n"
        f"Я <b>{BOT_NAME}</b> — помогу настроить твой VPN.\n\n"
        "👇 Нажмите <b>/vpn</b>, чтобы начать."
    )

    await update.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=main_keyboard(update.effective_user.id),
    )


# ===== Info sections =====

def make_placeholder() -> str:
    base = "Этот раздел пока в разработке."
    if SUPPORT_TG_USERNAME:
        return f"{base}\nПо вопросам — {SUPPORT_TG_USERNAME}"
    return base


PLACEHOLDER = make_placeholder()


async def on_how_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    if INSTALL_GUIDE_URL:
        await update.callback_query.message.reply_text(INSTALL_GUIDE_URL)
    else:
        await update.callback_query.message.reply_text("Инструкции пока недоступны.")


async def on_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    if SUPPORT_TG_USERNAME:
        await update.callback_query.message.reply_text(
            f"🤝 Поддержка\n\nНапишите нам: {SUPPORT_TG_USERNAME}"
        )
    else:
        await update.callback_query.message.reply_text(PLACEHOLDER)


async def on_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(PLACEHOLDER)


async def on_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.reply_text("⛔ Доступ запрещён.")
        return

    await query.message.reply_text(PLACEHOLDER)


# ===== VPN logic =====

async def on_get_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    name = user.full_name or user.username or "client"

    devices = storage.get_peers_by_telegram_id(user.id)

    if devices:
        await query.message.reply_text(
            "ℹ️ У вас уже есть активный VPN-доступ.\n\n"
            "Отправляю текущую конфигурацию 👇"
        )
    else:
        if len(devices) >= MAX_DEVICES_PER_USER:
            await query.message.reply_text(
                "❗ Достигнут лимит устройств.\n"
                "Удалите текущее устройство, чтобы добавить новое."
            )
            return

    try:
        config = get_or_create_peer_and_config(
            telegram_id=user.id,
            name=name,
            ttl_days=30,
        )
    except ProvisionError as e:
        await query.message.reply_text(f"❌ Доступ недоступен:\n{e}")
        return

    filename = f"{safe_filename(BOT_NAME)}.conf"

    await query.message.reply_document(
        document=config.encode(),
        filename=filename,
        caption="✅ Ваш конфигурационный файл WireGuard.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📡 Как установить", callback_data="how_install")]
        ]),
    )


async def on_check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    devices = storage.get_peers_by_telegram_id(query.from_user.id)
    peer = devices[0] if devices else None

    if not peer:
        msg = "❌ Доступ не найден."
        if SUPPORT_TG_USERNAME:
            msg += f"\nОбратитесь: {SUPPORT_TG_USERNAME}"
        await query.message.reply_text(msg)
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


# ===== Commands =====

async def cmd_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.full_name or user.username or "client"

    devices = storage.get_peers_by_telegram_id(user.id)
    if devices:
        await update.message.reply_text(
            "ℹ️ У вас уже есть активный VPN-доступ.\n"
            "Отправляю текущую конфигурацию 👇"
        )

    try:
        config = get_or_create_peer_and_config(
            telegram_id=user.id,
            name=name,
            ttl_days=30,
        )
    except ProvisionError as e:
        await update.message.reply_text(f"❌ Доступ недоступен:\n{e}")
        return

    filename = f"{safe_filename(BOT_NAME)}.conf"

    await update.message.reply_document(
        document=config.encode(),
        filename=filename,
        caption="✅ Ваш конфигурационный файл WireGuard.",
    )


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    await update.message.reply_text("Админ-раздел в разработке.")


# ===== Main =====

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    restore_peers_on_startup()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vpn", cmd_vpn))
    app.add_handler(CommandHandler("admin", admin_help))

    # callbacks — VPN
    app.add_handler(CallbackQueryHandler(on_get_access, pattern="^get_access$"))
    app.add_handler(CallbackQueryHandler(on_check_access, pattern="^check_access$"))

    # callbacks — info / service
    app.add_handler(CallbackQueryHandler(on_how_install, pattern="^how_install$"))
    app.add_handler(CallbackQueryHandler(on_support, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(on_promo, pattern="^promo$"))
    app.add_handler(CallbackQueryHandler(on_admin_panel, pattern="^admin_panel$"))

    # background jobs
    if app.job_queue:
        app.job_queue.run_repeating(expire_peers_job, interval=60, first=10)

    logger.info("Telegram bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

