import time
from typing import Optional

from bot import storage
from bot import wg


class ProvisionError(Exception):
    pass


def get_or_create_peer_and_config(
    telegram_id: int,
    name: str,
    ttl_days: Optional[int] = None
) -> str:
    """
    Main provisioning entrypoint.

    - One Telegram ID = one peer
    - One peer = one permanent config
    - Config content is always identical
    """

    # 1. Ensure DB is ready
    storage.init_db()

    # 2. Check if peer already exists
    peer = storage.get_peer_by_telegram_id(telegram_id)

    if peer:
        # Peer exists — just ensure it's enabled if not expired
        if peer["enabled"]:
            return wg.generate_client_config(
                peer["private_key"],
                peer["ip"]
            )

        # Peer exists but disabled (expired or manually revoked)
        raise ProvisionError("Access is disabled or expired")

    # 3. New peer provisioning
    private_key, public_key = wg.generate_keypair()
    ip = storage.get_next_ip()

    expires_at = None
    if ttl_days is not None:
        expires_at = int(time.time()) + ttl_days * 86400

    # 4. Persist peer
    storage.create_peer(
        telegram_id=telegram_id,
        name=name,
        private_key=private_key,
        public_key=public_key,
        ip=ip,
        expires_at=expires_at
    )

    # 5. Enable peer in WireGuard
    wg.enable_peer(public_key, ip)

    # 6. Generate and return config
    return wg.generate_client_config(
        private_key,
        ip
    )

