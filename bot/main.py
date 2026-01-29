# Updated main.py with Admin Promo Codes
import os
import re
import logging
import time
import random
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from bot import storage, wg, vless
from bot.provision import get_or_create_peer_and_config, ProvisionError
from bot.vless_provision import get_or_create_vless_config, VLESSProvisionError

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

    # Get protocol policy to decide what to restore
    policy = storage.get_protocol_policy()

    # Restore WireGuard peers if enabled
    if policy["wireguard_enabled"]:
        peers = storage.get_peers_for_restore(now_ts)
        if peers:
            restored = 0
            for peer in peers:
                try:
                    wg.enable_peer(peer["public_key"], peer["ip"])
                    restored += 1
                except wg.WireGuardError as e:
                    logger.error(
                        "Failed to enable WireGuard peer %s (%s): %s",
                        peer["public_key"],
                        peer["ip"],
                        e,
                    )
            logger.info("Restored %d WireGuard peers", restored)
        else:
            logger.info("No WireGuard peers to restore")

    # Restore VLESS peers if enabled
    if policy["vless_enabled"]:
        vless_peers = storage.get_vless_peers_for_restore(now_ts)
        if vless_peers:
            restored = 0
            for peer in vless_peers:
                try:
                    vless.enable_client(peer["uuid"], peer["name"])
                    restored += 1
                except vless.VLESSError as e:
                    logger.error(
                        "Failed to enable VLESS client %s (%s): %s",
                        peer["uuid"],
                        peer["name"],
                        e,
                    )
            logger.info("Restored %d VLESS clients", restored)
        else:
            logger.info("No VLESS clients to restore")


async def expire_peers_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job to disable expired peers"""
    now_ts = int(time.time())

    # Get protocol policy to decide what to check
    policy = storage.get_protocol_policy()

    # Expire WireGuard peers if enabled
    if policy["wireguard_enabled"]:
        peers = storage.get_expired_peers(now_ts)
        if peers:
            logger.info(
                "Found %d expired WireGuard peer(s) to disable", len(peers))
            for peer in peers:
                try:
                    wg.disable_peer(peer["public_key"])
                    storage.set_enabled(peer["telegram_id"], False)
                    logger.info("Disabled expired WireGuard peer: %s (IP: %s)",
                                peer["public_key"][:16], peer["ip"])
                except wg.WireGuardError as e:
                    logger.error(
                        "Failed to disable expired WireGuard peer %s (%s): %s",
                        peer["public_key"],
                        peer["ip"],
                        e,
                    )

    # Expire VLESS peers if enabled
    if policy["vless_enabled"]:
        vless_peers = storage.get_expired_vless_peers(now_ts)
        if vless_peers:
            logger.info(
                "Found %d expired VLESS client(s) to disable", len(vless_peers))
            for peer in vless_peers:
                try:
                    # First update DB, then remove from Xray
                    storage.set_vless_enabled(peer["telegram_id"], False)
                    vless.disable_client(peer["uuid"])
                    logger.info("Disabled expired VLESS client: %s (%s)",
                                peer["uuid"], peer["name"])
                except vless.VLESSError as e:
                    logger.error(
                        "Failed to disable expired VLESS client %s (%s): %s",
                        peer["uuid"],
                        peer["name"],
                        e,
                    )


def main_keyboard(user_id=None):
    buttons = [
        [InlineKeyboardButton("🔐 Получить VPN", callback_data="get_access")],
        [InlineKeyboardButton("ℹ️ Мой доступ", callback_data="check_access"), InlineKeyboardButton(
            "📡 Как установить", callback_data="how_install")],
        [InlineKeyboardButton("🤝 Поддержка", callback_data="support"), InlineKeyboardButton(
            "🎟 Ввести промокод", callback_data="promo")],
        [InlineKeyboardButton("💬 Частые вопросы", callback_data="faq")],
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
        [InlineKeyboardButton("🔧 Управление протоколами",
                              callback_data="admin_protocols")],
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

    # Generate unique promo code (retry if collision)
    max_attempts = 10
    code = None
    for attempt in range(max_attempts):
        candidate = generate_promo(days).upper()
        # Check if code already exists
        existing = storage.get_promo_code(candidate)
        if not existing:
            code = candidate
            break

    if not code:
        await q.message.reply_text(
            "❌ Не удалось создать уникальный промокод. Попробуйте еще раз.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]
            ])
        )
        return

    # Save promo code to database
    storage.save_promo_code(code, days, q.from_user.id)

    # Send promo code as separate message for easy copying
    await q.message.reply_text(f"<code>{code}</code>", parse_mode="HTML")

    text = (
        f"✅ Промокод создан на {days} дней\n\n"
        f"📝 Как воспользоваться:\n"
        f"1. Нажмите 🎟 Ввести промокод в главном меню\n"
        f"2. Отправьте промокод\n"
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


async def on_admin_protocols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show protocol management panel"""
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("⛔ Доступ запрещён")
        return

    policy = storage.get_protocol_policy()

    # Build status text
    wg_status = "✅" if policy['wireguard_enabled'] else "⚪"
    vless_status = "✅" if policy['vless_enabled'] else "⚪"

    wg_label = "WireGuard"
    vless_label = "VLESS Reality"

    # The active protocol is the one that's enabled
    active_protocol = "WireGuard" if policy['wireguard_enabled'] else "VLESS Reality"

    text = (
        "🔧 <b>Управление протоколами</b>\n\n"
        f"{wg_status} {wg_label}\n"
        f"{vless_status} {vless_label}\n\n"
        f"<b>Активный протокол:</b> {active_protocol}\n\n"
        "<i>Только один протокол может быть активен одновременно</i>"
    )

    # Build keyboard - show button to enable the inactive protocol
    kb = []

    if policy['wireguard_enabled']:
        kb.append([InlineKeyboardButton("🔄 Переключить на VLESS",
                  callback_data="proto_enable_vless")])
    else:
        kb.append([InlineKeyboardButton("🔄 Переключить на WireGuard",
                  callback_data="proto_enable_wireguard")])

    kb.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])

    await q.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def on_proto_enable_wireguard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable WireGuard protocol (disables VLESS)"""
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return

    # Enable WireGuard, disable VLESS, set WireGuard as primary
    storage.set_protocol_policy(True, False, 'wireguard')
    await on_admin_protocols(update, context)


async def on_proto_enable_vless(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable VLESS protocol (disables WireGuard)"""
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return

    # Enable VLESS, disable WireGuard, set VLESS as primary
    storage.set_protocol_policy(False, True, 'vless')
    await on_admin_protocols(update, context)


async def on_get_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    name = user.full_name or user.username or "client"

    # Check protocol policy to determine which config to generate
    policy = storage.get_protocol_policy()
    primary = policy['primary_protocol']

    # Check if user already has access
    if primary == 'wireguard':
        devices = storage.get_peers_by_telegram_id(user.id)
        has_access = len(devices) > 0
    else:  # vless
        peer = storage.get_vless_peer_by_telegram_id(user.id)
        has_access = peer is not None and peer["enabled"]

    if has_access:
        await query.message.reply_text(
            "ℹ️ У вас уже есть активный VPN-доступ.\n\n"
            "Отправляю текущую конфигурацию 👇"
        )

    # Generate config based on primary protocol
    try:
        if primary == 'wireguard':
            config = get_or_create_peer_and_config(
                telegram_id=user.id,
                name=name,
                ttl_days=30,
            )

            # Send as .conf file
            filename = f"{safe_filename(BOT_NAME)}.conf"
            await query.message.reply_document(
                document=config.encode(),
                filename=filename,
                caption="✅ Ваш конфигурационный файл WireGuard.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📡 Как установить",
                                          callback_data="how_install")],
                    [InlineKeyboardButton("🏠 Главное меню",
                                          callback_data="back_to_main")],
                ]),
            )
        else:  # vless
            vless_link = get_or_create_vless_config(
                telegram_id=user.id,
                name=name,
                ttl_days=30,
            )

            # Send as text with vless:// link
            caption = (
                "✅ Ваша конфигурация VLESS Reality\n\n"
                "Скопируйте ссылку ниже и добавьте в клиент VPN:"
            )
            await query.message.reply_text(
                f"{caption}\n\n<code>{vless_link}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📡 Как установить",
                                          callback_data="how_install")],
                    [InlineKeyboardButton("🏠 Главное меню",
                                          callback_data="back_to_main")],
                ]),
            )
    except (ProvisionError, VLESSProvisionError) as e:
        await query.message.reply_text(f"❌ Доступ недоступен:\n{e}")
        return


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
        f"🔑 ID клиента: <code>{query.from_user.id}</code>"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def on_how_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await update.callback_query.message.reply_text(
        INSTALL_GUIDE_URL or "Недоступно",
        reply_markup=kb
    )


async def on_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])

    if SUPPORT_TG_USERNAME:
        await update.callback_query.message.reply_text(
            f"🤝 Поддержка\n\nНапишите нам: {SUPPORT_TG_USERNAME}",
            reply_markup=kb
        )
    else:
        await update.callback_query.message.reply_text(
            "🤝 Поддержка\n\nКонтакт не настроен",
            reply_markup=kb
        )


async def on_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['waiting_for_promo'] = True

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])

    await update.callback_query.message.reply_text(
        "🎟 <b>Введите промокод</b>\n\n"
        "Промокод имеет формат: XX-XXXX-XXD\n"
        "Например: AB-JULY-30D\n\n"
        "Нажмите /cancel для отмены.",
        parse_mode="HTML",
        reply_markup=kb
    )


async def on_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    text = (
        "💬 <b>Частые вопросы</b>\n\n"
        "<b>Как получить доступ к VPN?</b>\n"
        "Активируйте промокод или используйте команду /vpn для получения конфигурации.\n\n"
        "<b>Как установить WireGuard?</b>\n"
        "Нажмите '📡 Как установить' в главном меню для получения инструкции.\n\n"
        "<b>Что делать если доступ истёк?</b>\n"
        "Активируйте новый промокод через '🎟 Ввести промокод'.\n\n"
        "<b>Как проверить статус доступа?</b>\n"
        "Используйте команду /status или нажмите 'ℹ️ Мой доступ'.\n\n"
        "<b>Как удалить VPN?</b>\n"
        "Используйте команду /remove для обращения в поддержку."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def on_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    q = update.callback_query
    await q.answer()

    # Clear any pending operations
    context.user_data['waiting_for_promo'] = False

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

    # Convert to uppercase for consistency (case-insensitive)
    code = update.message.text.strip().upper()

    # Check promo code format
    if not re.match(r'^[A-Z0-9]{2}-[A-Z]{4}-\d+D$', code):
        await update.message.reply_text(
            "❌ Неверный формат промокода.\n\n"
            "Промокод должен иметь формат: XX-XXXX-XXD\n"
            "Например: AB-JULY-30D\n\n"
            "Попробуйте еще раз:"
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

    # Reset flag only after successful validation
    context.user_data['waiting_for_promo'] = False

    # Activate promo code
    days = promo['days']
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name or update.effective_user.username or "client"

    # Check protocol policy to determine which peer to extend
    policy = storage.get_protocol_policy()
    primary = policy['primary_protocol']

    if primary == 'wireguard':
        # Get WireGuard peer
        peer = storage.get_peer_by_telegram_id(user_id)
    else:  # vless
        # Get VLESS peer
        peer = storage.get_vless_peer_by_telegram_id(user_id)

    if peer:
        # Update expiration date
        current_expires = peer['expires_at'] or int(time.time())
        # If expired, start from current time
        if current_expires < int(time.time()):
            current_expires = int(time.time())
        new_expires = current_expires + (days * 24 * 60 * 60)

        if primary == 'wireguard':
            storage.update_expiry(user_id, new_expires)

            # Enable peer in WireGuard if it was disabled
            if not peer['enabled']:
                try:
                    wg.enable_peer(peer['public_key'], peer['ip'])
                    storage.set_enabled(user_id, True)
                    logger.info(
                        f"Re-enabled WireGuard peer for user {user_id} after promo activation")
                except wg.WireGuardError as e:
                    logger.error(
                        f"Failed to enable WireGuard peer for user {user_id}: {e}")
        else:  # vless
            storage.update_vless_expiry(user_id, new_expires)

            # Enable peer in Xray if it was disabled
            if not peer['enabled']:
                try:
                    email = f"tg_{user_id}"
                    vless.enable_client(peer['uuid'], email)
                    storage.set_vless_enabled(user_id, True)
                    logger.info(
                        f"Re-enabled VLESS client for user {user_id} after promo activation")
                except vless.VLESSError as e:
                    logger.error(
                        f"Failed to enable VLESS client for user {user_id}: {e}")

        expires_date = datetime.fromtimestamp(
            new_expires).strftime('%d.%m.%Y %H:%M')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏠 Главное меню", callback_data="back_to_main")],
        ])
        await update.message.reply_text(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"Добавлено: {days} дней\n"
            f"Доступ продлён до: {expires_date}",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        # Create new peer with expiration
        expires_at = int(time.time()) + (days * 24 * 60 * 60)
        expires_date = datetime.fromtimestamp(
            expires_at).strftime('%d.%m.%Y %H:%M')

        try:
            if primary == 'wireguard':
                config_path = get_or_create_peer_and_config(
                    user_id, user_name, expires_at)
                logger.info(
                    f"Created new WireGuard peer for user {user_id} with {days} days access")
            else:  # vless
                vless_link = get_or_create_vless_config(
                    user_id, user_name, ttl_days=days)
                logger.info(
                    f"Created new VLESS client for user {user_id} with {days} days access")

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🏠 Главное меню", callback_data="back_to_main")],
            ])
            await update.message.reply_text(
                f"✅ <b>Промокод активирован!</b>\n\n"
                f"Вам предоставлен доступ на {days} дней до {expires_date}.\n\n"
                f"Используйте команду /vpn для получения конфигурации.",
                parse_mode="HTML",
                reply_markup=kb
            )
        except (ProvisionError, VLESSProvisionError) as e:
            await update.message.reply_text(
                f"❌ Ошибка при создании конфигурации: {e}"
            )
            logger.error(f"Failed to create peer for user {user_id}: {e}")
            return

    # Mark promo code as used
    storage.activate_promo_code(code, user_id)
    logger.info(f"Promo code {code} activated by user {user_id}")


# ===== Commands =====

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"📖 <b>Справка {BOT_NAME}</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start - Главное меню\n"
        "/vpn - Получить VPN конфигурацию\n"
        "/status - Проверить статус доступа\n"
        "/help - Показать эту справку\n"
        "/remove - Удалить VPN доступ\n\n"
        "<b>Возможности:</b>\n"
        "• Безопасное VPN подключение\n"
        "• Промокоды для активации доступа\n"
        "• Автоматическое управление сроком действия\n"
        "• Простая установка на всех устройствах"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation (e.g., promo code input)"""
    if context.user_data.get('waiting_for_promo'):
        context.user_data['waiting_for_promo'] = False
        await update.message.reply_text(
            "❌ Ввод промокода отменен.",
            reply_markup=main_keyboard(update.effective_user.id)
        )
    else:
        await update.message.reply_text(
            "Нет активных операций для отмены.",
            reply_markup=main_keyboard(update.effective_user.id)
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    devices = storage.get_peers_by_telegram_id(user_id)
    peer = devices[0] if devices else None

    if not peer:
        msg = "❌ Доступ не найден.\n\n"
        if SUPPORT_TG_USERNAME:
            msg += f"Обратитесь: {SUPPORT_TG_USERNAME}"
        else:
            msg += "Используйте промокод для активации доступа."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏠 Главное меню", callback_data="back_to_main")],
        ])
        await update.message.reply_text(msg, reply_markup=kb)
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

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await update.message.reply_text(text, reply_markup=kb)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    msg = "⚠️ <b>Удаление VPN доступа</b>\n\n"
    msg += "Для удаления VPN доступа обратитесь в поддержку.\n"

    if SUPPORT_TG_USERNAME:
        msg += f"Напишите нам: {SUPPORT_TG_USERNAME}"
    else:
        msg += "Контакт поддержки не настроен."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main")],
    ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


async def cmd_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.full_name or user.username or "client"

    # Check protocol policy to determine which config to generate
    policy = storage.get_protocol_policy()
    primary = policy['primary_protocol']

    # Check if user already has access
    if primary == 'wireguard':
        devices = storage.get_peers_by_telegram_id(user.id)
        has_access = len(devices) > 0
    else:  # vless
        peer = storage.get_vless_peer_by_telegram_id(user.id)
        has_access = peer is not None and peer["enabled"]

    if has_access:
        await update.message.reply_text(
            "ℹ️ У вас уже есть активный VPN-доступ.\n"
            "Отправляю текущую конфигурацию 👇"
        )

    # Generate config based on primary protocol
    try:
        if primary == 'wireguard':
            config = get_or_create_peer_and_config(
                telegram_id=user.id,
                name=name,
                ttl_days=30,
            )

            # Send as .conf file
            filename = f"{safe_filename(BOT_NAME)}.conf"
            await update.message.reply_document(
                document=config.encode(),
                filename=filename,
                caption="✅ Ваш конфигурационный файл WireGuard.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📡 Как установить",
                                          callback_data="how_install")],
                    [InlineKeyboardButton("🏠 Главное меню",
                                          callback_data="back_to_main")],
                ]),
            )
        else:  # vless
            vless_link = get_or_create_vless_config(
                telegram_id=user.id,
                name=name,
                ttl_days=30,
            )

            # Send as text with vless:// link
            caption = (
                "✅ Ваша конфигурация VLESS Reality\n\n"
                "Скопируйте ссылку ниже и добавьте в клиент VPN:"
            )
            await update.message.reply_text(
                f"{caption}\n\n<code>{vless_link}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📡 Как установить",
                                          callback_data="how_install")],
                    [InlineKeyboardButton("🏠 Главное меню",
                                          callback_data="back_to_main")],
                ]),
            )
    except (ProvisionError, VLESSProvisionError) as e:
        await update.message.reply_text(f"❌ Доступ недоступен:\n{e}")
        return


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
    # Build application with job queue enabled
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    restore_peers_on_startup()

    # Add periodic job to check and disable expired peers every 30 minutes
    # Starts after 60 seconds, then runs every 1800 seconds (30 minutes)
    if app.job_queue:
        app.job_queue.run_repeating(expire_peers_job, interval=1800, first=60)
        logger.info("Expiry checking job scheduled: runs every 30 minutes")
    else:
        logger.warning("JobQueue is not available, expiry checking disabled")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vpn", cmd_vpn))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("remove", cmd_remove))
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
        on_admin_protocols, pattern="^admin_protocols$"))
    app.add_handler(CallbackQueryHandler(
        on_proto_enable_wireguard, pattern="^proto_enable_wireguard$"))
    app.add_handler(CallbackQueryHandler(
        on_proto_enable_vless, pattern="^proto_enable_vless$"))
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
    app.add_handler(CallbackQueryHandler(on_faq, pattern="^faq$"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_promo_code))
    app.run_polling()


if __name__ == '__main__':
    main()
