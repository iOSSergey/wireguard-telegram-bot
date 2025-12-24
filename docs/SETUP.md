# Server Setup Guide  
WireGuard TelegramBot

This document describes how to deploy the project on a clean server.

Target system:
- Debian 12 (Bookworm)
- Minimal installation
- No Docker, no web panels

---

## Step 1. Change root password

Immediately change the root password after first login:

```bash
passwd
```

---

## Step 2. Base system preparation and update

Update system packages and install required software:

```bash
apt update && apt upgrade -y
apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  ufw \
  curl \
  wget \
  vim \
  rsync
```

---

## Step 3. Swap file setup (for small servers)

Check existing swap:

```bash
swapon --show
```

Optimize swap usage (for low-memory servers, e.g. 512 MB RAM):

```bash
sysctl vm.swappiness=10
echo 'vm.swappiness=10' >> /etc/sysctl.conf
sysctl -p
```

---

## Step 4. SSH key-based access and hardening

⚠️ Make sure SSH key-based login already works BEFORE running these commands.

Disable password authentication and harden SSH:

```bash
sed -i \
  -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
  -e 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' \
  /etc/ssh/sshd_config
```

Verify configuration:

```bash
grep -E 'PasswordAuthentication|PubkeyAuthentication' /etc/ssh/sshd_config
```

Restart SSH service:

```bash
systemctl restart ssh
```

⚠️ Do NOT close your current SSH session until you confirm
that a new SSH connection works with key authentication.

---

## Step 5. Enable automatic security updates

Install unattended upgrades:

```bash
apt install -y unattended-upgrades apt-listchanges
```

Enable security updates only:

```bash
dpkg-reconfigure --priority=low unattended-upgrades
```

Force-enable automatic reboot after updates:

```bash
cat <<EOF >/etc/apt/apt.conf.d/51-auto-reboot
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
EOF
```

---

## Step 6. WireGuard installation and configuration

Install WireGuard:

```bash
apt install -y wireguard
```

Create WireGuard directory:

```bash
mkdir -p /etc/wireguard
chmod 700 /etc/wireguard
```

Generate server keys:

```bash
wg genkey | tee /etc/wireguard/server.key | wg pubkey > /etc/wireguard/server.pub
```

Create server config from template:

```bash
cp wireguard/wg0.conf.example /etc/wireguard/wg0.conf
```

Edit WireGuard configuration:

```bash
nano /etc/wireguard/wg0.conf
```

Set server private key and networking logic.

Example `[Interface]` section:

```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <SERVER_PRIVATE_KEY>

PostUp   = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o ens3 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o ens3 -j MASQUERADE
```

⚠️ Replace `ens3` with your actual WAN interface:
```bash
ip route | grep default
```

Set permissions:

```bash
chmod 600 /etc/wireguard/wg0.conf
```

Enable and start WireGuard:

```bash
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0
```

Verify:

```bash
wg show
```

---

## Step 7. Tun2Socks (placeholder)

⚠️ Placeholder

Tun2Socks will be installed and configured here.
This step requires routing and networking adjustments
and will be implemented later.

---

## Step 8. Project setup

Clone repository:

```bash
git clone <REPO_URL> /opt/wireguard-telegram-bot
cd /opt/wireguard-telegram-bot
```

Create virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r bot/requirements.txt
```

---

## Step 9. Environment variables

Create runtime config:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

---

## Step 10. systemd services

Copy unit files:

```bash
cp systemd/*.service /etc/systemd/system/
cp systemd/*.timer /etc/systemd/system/
```

Reload systemd:

```bash
systemctl daemon-reload
```

Enable and start services:

```bash
systemctl enable vpn-bot vpn-expiry.timer
systemctl start vpn-bot vpn-expiry.timer
```

---

## Step 11. Verification

```bash
systemctl status vpn-bot
systemctl list-timers
```

---

## Notes

- Root password is changed immediately
- SSH password login is disabled
- Only security updates are installed automatically
- Server may reboot automatically
- Swap optimized for low-memory VPS
- WireGuard manages its own networking (NAT, forwarding)
- Secrets are NOT stored in Git
- No Docker, no web panels

