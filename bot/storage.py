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


def get_next_ip(subnet_prefix: str = "10.8.0.") -> str:
    """
    Allocate next IP strictly based on DB state.
    Manual / legacy peers must be outside FIRST_CLIENT_IP range.
    """
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

