"""
VLESS provisioning logic - analogous to provision.py for WireGuard
Handles VLESS peer creation and configuration generation
"""
import time
import uuid
from typing import Optional

from bot import storage
from bot import vless


class VLESSProvisionError(Exception):
    """VLESS provisioning error"""
    pass


def get_or_create_vless_config(
    telegram_id: int,
    name: str,
    ttl_days: Optional[int] = None
) -> str:
    """
    Main VLESS provisioning entrypoint.

    - One Telegram ID = one VLESS peer
    - One peer = one permanent UUID and config

    Args:
        telegram_id: Telegram user ID
        name: User name for identification
        ttl_days: Optional TTL in days for access expiration

    Returns:
        VLESS URL (vless:// link)
    """

    # 1. Ensure DB is ready
    storage.init_db()

    # 2. Check if VLESS peer already exists
    peer = storage.get_vless_peer_by_telegram_id(telegram_id)

    if peer:
        # Peer exists - check if enabled
        if peer["enabled"]:
            # Generate config from existing UUID
            vless_link = vless.generate_vless_link(peer["uuid"], name)
            return vless_link

        # Peer exists but disabled (expired or manually revoked)
        raise VLESSProvisionError("Access is disabled or expired")

    # 3. New peer provisioning - generate UUID
    client_uuid = str(uuid.uuid4())

    expires_at = None
    if ttl_days is not None:
        expires_at = int(time.time()) + ttl_days * 86400

    # 4. Persist peer in database
    storage.create_vless_peer(
        telegram_id=telegram_id,
        name=name,
        uuid=client_uuid,
        expires_at=expires_at
    )

    # 5. Enable peer in Xray system
    try:
        # Use telegram_id as email for identification in Xray logs
        email = f"tg_{telegram_id}"
        vless.enable_client(client_uuid, email)
    except vless.VLESSError as e:
        # Rollback database changes
        storage.delete_vless_peer(telegram_id)
        raise VLESSProvisionError(f"Failed to enable VLESS client: {e}")

    # 6. Generate and return VLESS link
    vless_link = vless.generate_vless_link(client_uuid, name)

    return vless_link
