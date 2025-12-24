import os
import subprocess
from typing import Tuple

# ===== Environment configuration =====

WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
WG_SERVER_PUBLIC_KEY_PATH = os.getenv(
    "WG_SERVER_PUBLIC_KEY_PATH",
    "/etc/wireguard/server.pub"
)
WG_ENDPOINT = os.getenv("WG_ENDPOINT")  # REQUIRED
WG_ALLOWED_IPS = os.getenv("WG_ALLOWED_IPS", "0.0.0.0/0")
WG_DNS = os.getenv("WG_DNS", "1.1.1.1")

if not WG_ENDPOINT:
    raise RuntimeError("WG_ENDPOINT is not set in environment")


class WireGuardError(Exception):
    pass


def _run(cmd, input_text=None) -> str:
    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise WireGuardError(e.stderr.strip())


# ===== PUBLIC API =====

def generate_keypair() -> Tuple[str, str]:
    """
    Generate WireGuard private/public keypair.
    """
    private_key = _run(["wg", "genkey"])
    public_key = _run(["wg", "pubkey"], input_text=private_key)
    return private_key, public_key


def get_server_public_key() -> str:
    with open(WG_SERVER_PUBLIC_KEY_PATH, "r") as f:
        return f.read().strip()


def enable_peer(public_key: str, ip: str):
    _run([
        "wg", "set", WG_INTERFACE,
        "peer", public_key,
        "allowed-ips", f"{ip}/32"
    ])


def disable_peer(public_key: str):
    _run([
        "wg", "set", WG_INTERFACE,
        "peer", public_key,
        "remove"
    ])


def generate_client_config(
    client_private_key: str,
    client_ip: str
) -> str:
    server_public_key = get_server_public_key()

    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_ip}/32
DNS = {WG_DNS}

[Peer]
PublicKey = {server_public_key}
Endpoint = {WG_ENDPOINT}
AllowedIPs = {WG_ALLOWED_IPS}
PersistentKeepalive = 25
"""

