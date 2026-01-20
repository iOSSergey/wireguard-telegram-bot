# Updated main.py with Admin Promo Codes
import os
import re
import logging
import time
import random
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from bot import storage, wg
from bot.provision import get_or_create_peer_and_config, ProvisionError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment")

ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID")) if os.getenv(
    "ADMIN_TG_ID", "").isdigit() else None
BOT_NAME = os.getenv("BOT_NAME", "VPN Bot")
SUPPORT_TG_USERNAME = os.getenv("SUPPORT_TG_USERNAME")
INSTALL_GUIDE_URL = os.getenv("INSTALL_GUIDE_URL")
MAX_DEVICES_PER_USER = int(os.getenv("MAX_DEVICES_PER_USER", "1"))

WORDS = ["JULY", "AUGU", "SEPT", "OCTO"]


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
    """Periodic job to disable expired peers"""
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


def main_keyboard(user_id=None):
    buttons = [
        [InlineKeyboardButton("🔐 Получить VPN", callback_data="get_access")],
        [InlineKeyboardButton("ℹ️ Мой доступ", callback_data="check_access"), InlineKeyboardButton(
            "📡 Как установить", callback_data="how_install")],
        [InlineKeyboardButton("🤝 Поддержка", callback_data="support"), InlineKeyboardButton(
            "🎟 Ввести промокод", callback_data="promo")],
    ]
    if user_id and is_admin(user_id):
        buttons.append([InlineKeyboardButton(
            "🛠 Администрирование", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


# ===== Handlers =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"👋 Привет!\n"
        f"Я <b>{BOT_NAME}</b> — помогу настроить твой VPN.\n\n"
        "💠 Что я умею:\n"
        "• сделать защищённый VPN-канал\n"
        "• выдать конфигурацию WireGuard\n"
        "• помочь подключиться\n\n"
        "👇 Нажмите <b>/vpn</b>, чтобы получить доступ."
    )

    await update.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=main_keyboard(update.effective_user.id),
    )


async def on_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("⛔ Доступ запрещён")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Создать промокод",
                              callback_data="admin_promo")],
        [InlineKeyboardButton("ℹ️ Показать статистику",
                              callback_data="admin_stats")],
        [InlineKeyboardButton(
            "🏠 Главное меню", callback_data="back_to_main")],
    ])
    await q.message.reply_text("🛠 Администрирование", reply_markup=kb)


async def on_admin_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("⛔ Доступ запрещён")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("7 дней", callback_data="promo_days_7"), InlineKeyboardButton(
            "15 дней", callback_data="promo_days_15")],
        [InlineKeyboardButton("30 дней", callback_data="promo_days_30"), InlineKeyboardButton(
            "60 дней", callback_data="promo_days_60")],
        [InlineKeyboardButton("90 дней", callback_data="promo_days_90"), InlineKeyboardButton(
            "365 дней", callback_data="promo_days_365")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
    ])
    await q.message.reply_text("🎟 Выберите срок промокода", reply_markup=kb)


def generate_promo(days: int) -> str:
    prefix = ''.join(random.choice(
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(2))
    word = WORDS[int(time.time()) % len(WORDS)]
    return f"{prefix}-{word}-{days}D"


async def on_promo_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    days = int(q.data.split('_')[-1])
    code = generate_promo(days).upper()  # Ensure uppercase

    # Save promo code to database
    storage.save_promo_code(code, days, q.from_user.id)

    text = (
        f"✅ Промокод создан:\n"
        f"<code>{code}</code>\n\n"
        f"📝 Как воспользоваться:\n"
        f"1. Нажмите 🎟 Ввести промокод в главном меню\n"
        f"2. Отправьте код <code>{code}</code>\n"
        f"3. Промокод активирует доступ на {days} дней"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Создать еще", callback_data="admin_promo")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
    ])
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def on_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("⛔ Доступ запрещён")
        return

    try:
        stats, recent = storage.get_promo_stats()

        text = "📊 <b>Статистика по промокодам</b>\n\n"
        text += f"Всего создано: {stats['total'] or 0}\n"
        text += f"Активировано: {stats['activated'] or 0}\n"
        text += f"Не использовано: {stats['unused'] or 0}\n\n"

        if recent:
            text += "<b>Последние 20 промокодов:</b>\n"
            for promo in recent:
                status = "✅" if promo['activated_at'] else "⏳"
                text += f"\n{status} <code>{promo['code']}</code> ({promo['days']} дн.)\n"
                text += f"  Создан: {datetime.fromtimestamp(promo['created_at']).strftime('%d.%m.%Y %H:%M')}\n"
                if promo['activated_at']:
                    text += f"  Активирован: {datetime.fromtimestamp(promo['activated_at']).strftime('%d.%m.%Y %H:%M')}\n"
        else:
            text += "<i>Промокодов пока нет</i>"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
        ])
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error in on_admin_stats: {e}")
        await q.message.reply_text(f"❌ Ошибка при получении статистики: {e}")


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
            [InlineKeyboardButton("📡 Как установить",
                                  callback_data="how_install")]
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
        expires = datetime.fromtimestamp(
            peer["expires_at"]).strftime("%d.%m.%Y %H:%M")
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


async def on_how_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(INSTALL_GUIDE_URL or "Недоступно")


async def on_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    if SUPPORT_TG_USERNAME:
        await update.callback_query.message.reply_text(
            f"🤝 Поддержка\n\nНапишите нам: {SUPPORT_TG_USERNAME}"
        )
    else:
        await update.callback_query.message.reply_text("🤝 Поддержка\n\nКонтакт не настроен")


async def on_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['waiting_for_promo'] = True
    await update.callback_query.message.reply_text(
        "🎟 <b>Введите промокод</b>\n\n"
        "Промокод имеет формат: XX-XXXX-XXD\n"
        "Например: AB-JULY-30D\n\n"
        "Отправьте промокод следующим сообщением.",
        parse_mode="HTML"
    )


async def on_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    q = update.callback_query
    await q.answer()

    text = (
        f"👋 Привет!\n"
        f"Я <b>{BOT_NAME}</b> — помогу настроить твой VPN.\n\n"
        "💠 Что я умею:\n"
        "• сделать защищённый VPN-канал\n"
        "• выдать конфигурацию WireGuard\n"
        "• помочь подключиться\n\n"
        "👇 Нажмите <b>/vpn</b>, чтобы получить доступ."
    )

    await q.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=main_keyboard(q.from_user.id),
    )


async def handle_promo_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Promo code activation handler"""
    if not context.user_data.get('waiting_for_promo'):
        return

    context.user_data['waiting_for_promo'] = False
    # Convert to uppercase for consistency (case-insensitive)
    code = update.message.text.strip().upper()

    # Check promo code format
    if not re.match(r'^[A-Z0-9]{2}-[A-Z]{4}-\d+D$', code):
        await update.message.reply_text(
            "❌ Неверный формат промокода.\n\n"
            "Промокод должен иметь формат: XX-XXXX-XXD\n"
            "Например: AB-JULY-30D"
        )
        return

    # Check promo code in database
    promo = storage.get_promo_code(code)

    if not promo:
        await update.message.reply_text(
            "❌ Промокод не найден.\n\n"
            "Проверьте правильность ввода и попробуйте снова."
        )
        return

    if promo['activated_at']:
        await update.message.reply_text(
            "❌ Этот промокод уже был использован.\n\n"
            f"Активирован: {datetime.fromtimestamp(promo['activated_at']).strftime('%d.%m.%Y %H:%M')}"
        )
        return

    # Additional check: days in code must match database
    code_days = int(code.split('-')[-1].rstrip('D'))
    if code_days != promo['days']:
        await update.message.reply_text(
            "❌ Промокод поврежден или недействителен.\n\n"
            "Обратитесь в поддержку."
        )
        logger.warning(
            f"Promo code mismatch: code={code}, code_days={code_days}, db_days={promo['days']}")
        return

    # Activate promo code
    days = promo['days']
    user_id = update.effective_user.id

    # Get current user
    peer = storage.get_peer_by_telegram_id(user_id)

    if peer:
        # Update expiration date
        current_expires = peer['expires_at'] or int(time.time())
        # If expired, start from current time
        if current_expires < int(time.time()):
            current_expires = int(time.time())
        new_expires = current_expires + (days * 24 * 60 * 60)
        storage.update_expiry(user_id, new_expires)

        expires_date = datetime.fromtimestamp(
            new_expires).strftime('%d.%m.%Y %H:%M')
        await update.message.reply_text(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"Добавлено: {days} дней\n"
            f"Доступ продлён до: {expires_date}",
            parse_mode="HTML"
        )
    else:
        # Create new user with access
        await update.message.reply_text(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"Вам предоставлен доступ на {days} дней.\n\n"
            f"Используйте команду /vpn для получения конфигурации.",
            parse_mode="HTML"
        )

    # Mark promo code as used
    storage.activate_promo_code(code, user_id)
    logger.info(f"Promo code {code} activated by user {user_id}")


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
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📡 Как установить",
                                  callback_data="how_install")]
        ]),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Доступ запрещён")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Создать промокод",
                              callback_data="admin_promo")],
        [InlineKeyboardButton("ℹ️ Показать статистику",
                              callback_data="admin_stats")],
    ])
    await update.message.reply_text("🛠 Администрирование", reply_markup=kb)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    restore_peers_on_startup()

    # Add periodic job to check and disable expired peers every 30 minutes
    app.job_queue.run_repeating(expire_peers_job, interval=1800, first=60)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vpn", cmd_vpn))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(
        on_admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(
        on_admin_promo, pattern="^admin_promo$"))
    app.add_handler(CallbackQueryHandler(
        on_promo_days, pattern="^promo_days_"))
    app.add_handler(CallbackQueryHandler(
        on_admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(
        on_back_to_main, pattern="^back_to_main$"))
    app.add_handler(CallbackQueryHandler(
        on_get_access, pattern="^get_access$"))
    app.add_handler(CallbackQueryHandler(
        on_check_access, pattern="^check_access$"))
    app.add_handler(CallbackQueryHandler(
        on_how_install, pattern="^how_install$"))
    app.add_handler(CallbackQueryHandler(on_support, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(on_promo, pattern="^promo$"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_promo_code))
    app.run_polling()


if __name__ == '__main__':
    main()
