import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path("bot/data.db")

# First IP issued by bot (reserve lower range for manual peers)
FIRST_CLIENT_IP = 10


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL UNIQUE,
            ip TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            days INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            activated_at INTEGER,
            activated_by INTEGER
        )
    """)
    conn.commit()
    conn.close()


def get_peer_by_telegram_id(telegram_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.execute(
        "SELECT * FROM peers WHERE telegram_id = ?",
        (telegram_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_peers_by_telegram_id(telegram_id: int) -> list[sqlite3.Row]:
    """
    Возвращает список peer'ов пользователя.
    Сейчас максимум 1, но архитектура готова к нескольким.
    """
    conn = get_db()
    cur = conn.execute(
        "SELECT * FROM peers WHERE telegram_id = ? ORDER BY id",
        (telegram_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def create_peer(
    telegram_id: int,
    name: str,
    private_key: str,
    public_key: str,
    ip: str,
    expires_at: Optional[int]
):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO peers (
            telegram_id,
            name,
            private_key,
            public_key,
            ip,
            created_at,
            expires_at,
            enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            telegram_id,
            name,
            private_key,
            public_key,
            ip,
            int(time.time()),
            expires_at
        )
    )
    conn.commit()
    conn.close()


def update_expiry(telegram_id: int, expires_at: int):
    conn = get_db()
    conn.execute(
        "UPDATE peers SET expires_at = ? WHERE telegram_id = ?",
        (expires_at, telegram_id)
    )
    conn.commit()
    conn.close()


def set_enabled(telegram_id: int, enabled: bool):
    conn = get_db()
    conn.execute(
        "UPDATE peers SET enabled = ? WHERE telegram_id = ?",
        (1 if enabled else 0, telegram_id)
    )
    conn.commit()
    conn.close()


def delete_peer(telegram_id: int):
    conn = get_db()
    conn.execute(
        "DELETE FROM peers WHERE telegram_id = ?",
        (telegram_id,)
    )
    conn.commit()
    conn.close()


def get_peers_for_restore(now_ts: int):
    conn = get_db()
    cur = conn.execute(
        """SELECT * FROM peers
        WHERE enabled = 1
          AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY id
        """,
        (now_ts,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_expired_peers(now_ts: int):
    conn = get_db()
    cur = conn.execute(
        """SELECT * FROM peers
        WHERE enabled = 1
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        ORDER BY id
        """,
        (now_ts,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_next_ip(subnet_prefix: str = "10.8.0.") -> str:
    conn = get_db()
    cur = conn.execute(
        "SELECT ip FROM peers ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return f"{subnet_prefix}{FIRST_CLIENT_IP}"

    last_ip = row["ip"]
    last_octet = int(last_ip.split(".")[-1])
    return f"{subnet_prefix}{last_octet + 1}"


def save_promo_code(code: str, days: int, created_by: int):
    """Сохраняет созданный промокод"""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO promo_codes (code, days, created_at, created_by)
        VALUES (?, ?, ?, ?)
        """,
        (code, days, int(time.time()), created_by)
    )
    conn.commit()
    conn.close()


def get_promo_code(code: str) -> Optional[sqlite3.Row]:
    """Возвращает информацию о промокоде"""
    conn = get_db()
    cur = conn.execute(
        "SELECT * FROM promo_codes WHERE code = ?",
        (code,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def activate_promo_code(code: str, activated_by: int):
    """Помечает промокод как активированный"""
    conn = get_db()
    conn.execute(
        """
        UPDATE promo_codes 
        SET activated_at = ?, activated_by = ? 
        WHERE code = ?
        """,
        (int(time.time()), activated_by, code)
    )
    conn.commit()
    conn.close()


def get_promo_stats():
    """Возвращает статистику по промокодам"""
    conn = get_db()
    cur = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN activated_at IS NOT NULL THEN 1 ELSE 0 END) as activated,
            SUM(CASE WHEN activated_at IS NULL THEN 1 ELSE 0 END) as unused
        FROM promo_codes
    """)
    stats = cur.fetchone()

    cur = conn.execute("""
        SELECT code, days, created_at, activated_at, activated_by
        FROM promo_codes
        ORDER BY created_at DESC
        LIMIT 20
    """)
    recent = cur.fetchall()

    conn.close()
    return stats, recent
