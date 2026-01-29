"""
VLESS Reality client management via Xray config.json
Handles adding/removing clients and reloading Xray service
"""
import os
import json
import subprocess
from pathlib import Path
from urllib.parse import quote


class VLESSError(Exception):
    """VLESS operation error"""
    pass


# Environment configuration
XRAY_CONFIG_PATH = os.getenv(
    "XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
XRAY_SERVICE = os.getenv("XRAY_SERVICE", "xray")
XRAY_SERVER_NAME = os.getenv("XRAY_SERVER_NAME")
XRAY_SERVER_ADDRESS = os.getenv("XRAY_SERVER_ADDRESS")
XRAY_PUBLIC_KEY = os.getenv("XRAY_PUBLIC_KEY")
XRAY_SHORT_ID = os.getenv("XRAY_SHORT_ID", "")
XRAY_CONFIG_PREFIX = os.getenv(
    "XRAY_CONFIG_PREFIX", "VPN")  # Prefix for config name

# Validate required environment variables
if not XRAY_SERVER_NAME:
    raise VLESSError("XRAY_SERVER_NAME environment variable is required")
if not XRAY_SERVER_ADDRESS:
    raise VLESSError("XRAY_SERVER_ADDRESS environment variable is required")
if not XRAY_PUBLIC_KEY:
    raise VLESSError("XRAY_PUBLIC_KEY environment variable is required")


def _load_config() -> dict:
    """Load Xray configuration from file"""
    try:
        with open(XRAY_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise VLESSError(f"Xray config not found: {XRAY_CONFIG_PATH}")
    except json.JSONDecodeError as e:
        raise VLESSError(f"Invalid Xray config JSON: {e}")


def _save_config(config: dict) -> None:
    """Save Xray configuration to file"""
    try:
        # Create backup
        backup_path = f"{XRAY_CONFIG_PATH}.backup"
        if Path(XRAY_CONFIG_PATH).exists():
            subprocess.run(
                ["cp", XRAY_CONFIG_PATH, backup_path],
                check=True,
                capture_output=True
            )

        # Save new configuration
        with open(XRAY_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise VLESSError(f"Failed to save Xray config: {e}")


def _find_vless_inbound(config: dict) -> dict:
    """
    Find inbound with VLESS protocol and Reality security.
    Returns first matching inbound or None.
    """
    inbounds = config.get("inbounds", [])
    for inbound in inbounds:
        if inbound.get("protocol") == "vless":
            # Check for Reality in streamSettings
            stream_settings = inbound.get("streamSettings", {})
            if stream_settings.get("security") == "reality":
                return inbound
    return None


def _reload_xray() -> None:
    """Restart Xray service after config change"""
    try:
        # First validate configuration
        result = subprocess.run(
            ["xray", "-test", "-config", XRAY_CONFIG_PATH],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            error_output = result.stderr or result.stdout
            raise VLESSError(
                f"Xray config validation failed:\n{error_output}\n"
                f"Return code: {result.returncode}"
            )

        # Restart service (Xray doesn't support reload)
        restart_result = subprocess.run(
            ["systemctl", "restart", XRAY_SERVICE],
            check=True,
            capture_output=True,
            timeout=10
        )
    except subprocess.TimeoutExpired:
        raise VLESSError("Xray restart timeout")
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
        raise VLESSError(f"Failed to restart Xray: {error_output}")
    except VLESSError:
        # Re-raise VLESSError as is
        raise
    except Exception as e:
        raise VLESSError(f"Xray restart error: {e}")


def enable_client(uuid: str, email: str = None) -> None:
    """
    Add VLESS client to Xray config and restart service.

    Args:
        uuid: Client UUID
        email: Optional email/identifier for logs
    """
    config = _load_config()

    # Find VLESS Reality inbound
    inbound = _find_vless_inbound(config)
    if not inbound:
        raise VLESSError("VLESS Reality inbound not found in Xray config")

    # Check if client already exists
    clients = inbound.get("settings", {}).get("clients", [])
    if any(c.get("id") == uuid for c in clients):
        # Client already exists, do nothing
        return

    # Add new client
    new_client = {
        "id": uuid,
        "flow": "xtls-rprx-vision",
        "email": email or f"user_{uuid[:8]}"
    }
    clients.append(new_client)

    # Save configuration
    _save_config(config)

    # Restart Xray
    _reload_xray()


def disable_client(uuid: str) -> None:
    """
    Remove VLESS client from Xray config and restart service.

    Args:
        uuid: Client UUID
    """
    config = _load_config()

    # Find VLESS Reality inbound
    inbound = _find_vless_inbound(config)
    if not inbound:
        raise VLESSError("VLESS Reality inbound not found in Xray config")

    # Remove client
    clients = inbound.get("settings", {}).get("clients", [])
    inbound["settings"]["clients"] = [
        c for c in clients if c.get("id") != uuid
    ]

    # Save configuration
    _save_config(config)

    # Restart Xray
    _reload_xray()


def generate_vless_link(uuid: str, user_name: str = None) -> str:
    """
    Generate VLESS Reality connection link for client.
    Simple scheme: VLESS + TCP + Reality

    Args:
        uuid: Client UUID
        user_name: User name (e.g. Telegram full_name) for identification

    Returns:
        VLESS URL in format vless://UUID@SERVER:PORT?params#name
    """
    # Build Reality parameters - minimal required set
    params = {
        "type": "tcp",
        "security": "reality",
        "pbk": XRAY_PUBLIC_KEY,
        "fp": "chrome",
        "sni": XRAY_SERVER_NAME,
        "flow": "xtls-rprx-vision"
    }

    # Add short ID if configured
    if XRAY_SHORT_ID:
        params["sid"] = XRAY_SHORT_ID

    # Build parameter string
    param_str = "&".join(f"{k}={v}" for k, v in params.items())

    # Build display name with prefix
    if user_name:
        display_name = f"{XRAY_CONFIG_PREFIX} - {user_name}"
    else:
        display_name = XRAY_CONFIG_PREFIX

    # URL-encode display name for proper handling of spaces and special characters
    encoded_name = quote(display_name, safe='')

    # Build VLESS URL
    # Format: vless://UUID@SERVER:PORT?params#name
    vless_url = f"vless://{uuid}@{XRAY_SERVER_ADDRESS}?{param_str}#{encoded_name}"

    return vless_url
