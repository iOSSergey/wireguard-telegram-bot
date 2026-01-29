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
    # VLESS peers table - separate from WireGuard peers
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vless_peers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            uuid TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Settings table for VPN mode (wireguard/vless/hybrid)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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


# ===== VLESS Peers Functions =====

def create_vless_peer(telegram_id: int, name: str, uuid: str, expires_at: int = None):
    """Create new VLESS peer"""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO vless_peers (telegram_id, name, uuid, created_at, expires_at, enabled)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (telegram_id, name, uuid, int(time.time()), expires_at)
    )
    conn.commit()
    conn.close()


def get_vless_peer_by_telegram_id(telegram_id: int) -> Optional[sqlite3.Row]:
    """Get VLESS peer by telegram ID"""
    conn = get_db()
    cur = conn.execute(
        "SELECT * FROM vless_peers WHERE telegram_id = ?",
        (telegram_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def delete_vless_peer(telegram_id: int):
    """Delete VLESS peer by telegram ID"""
    conn = get_db()
    conn.execute(
        "DELETE FROM vless_peers WHERE telegram_id = ?",
        (telegram_id,)
    )
    conn.commit()
    conn.close()


def update_vless_expiry(telegram_id: int, expires_at: int):
    """Update VLESS peer expiration date"""
    conn = get_db()
    conn.execute(
        "UPDATE vless_peers SET expires_at = ? WHERE telegram_id = ?",
        (expires_at, telegram_id)
    )
    conn.commit()
    conn.close()


def set_vless_enabled(telegram_id: int, enabled: bool):
    """Enable or disable VLESS peer"""
    conn = get_db()
    conn.execute(
        "UPDATE vless_peers SET enabled = ? WHERE telegram_id = ?",
        (1 if enabled else 0, telegram_id)
    )
    conn.commit()
    conn.close()


def get_vless_peers_for_restore(now_ts: int) -> list[sqlite3.Row]:
    """Get all VLESS peers that should be enabled (not expired)"""
    conn = get_db()
    cur = conn.execute(
        """
        SELECT * FROM vless_peers 
        WHERE enabled = 1 AND (expires_at IS NULL OR expires_at > ?)
        """,
        (now_ts,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_expired_vless_peers(now_ts: int) -> list[sqlite3.Row]:
    """Get all expired VLESS peers that are still enabled"""
    conn = get_db()
    cur = conn.execute(
        """
        SELECT * FROM vless_peers 
        WHERE enabled = 1 AND expires_at IS NOT NULL AND expires_at <= ?
        """,
        (now_ts,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ===== Settings Functions =====

def get_protocol_policy() -> dict:
    """
    Get current protocol policy.

    Returns:
        dict with keys:
            - wireguard_enabled: bool
            - vless_enabled: bool
            - primary_protocol: 'wireguard' or 'vless'

    Default: WireGuard enabled and primary, VLESS disabled
    """
    conn = get_db()
    cur = conn.execute(
        "SELECT value FROM settings WHERE key = 'protocol_policy'",
    )
    row = cur.fetchone()
    conn.close()

    if row:
        import json
        return json.loads(row['value'])

    # Default policy
    return {
        'wireguard_enabled': True,
        'vless_enabled': False,
        'primary_protocol': 'wireguard'
    }


def set_protocol_policy(wireguard_enabled: bool, vless_enabled: bool, primary_protocol: str):
    """
    Set protocol policy with validation.

    Args:
        wireguard_enabled: Enable WireGuard protocol
        vless_enabled: Enable VLESS protocol
        primary_protocol: Primary protocol ('wireguard' or 'vless')

    Raises:
        ValueError: If policy is invalid
    """
    # Validate policy
    if not wireguard_enabled and not vless_enabled:
        raise ValueError("At least one protocol must be enabled")

    if primary_protocol not in ['wireguard', 'vless']:
        raise ValueError("Primary protocol must be 'wireguard' or 'vless'")

    if primary_protocol == 'wireguard' and not wireguard_enabled:
        raise ValueError("Primary protocol must be enabled")

    if primary_protocol == 'vless' and not vless_enabled:
        raise ValueError("Primary protocol must be enabled")

    # Save policy as JSON
    import json
    policy = {
        'wireguard_enabled': wireguard_enabled,
        'vless_enabled': vless_enabled,
        'primary_protocol': primary_protocol
    }

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('protocol_policy', ?)",
        (json.dumps(policy),)
    )
    conn.commit()
    conn.close()
