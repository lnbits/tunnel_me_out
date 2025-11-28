import os
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from .crud import delete_tunnel, get_by_payment_hash, get_tunnel, save_tunnel
from .models import TunnelRecord

REMOTE_BASE = "https://satsy.co"
REMOTE_PUBLIC_ID = "N5iicNjZz2fyMZtiD3zvxT"
PRICE_PER_DAY = 100  # sats; keep in sync with remote pricing


async def fetch_existing(user_id: str) -> TunnelRecord | None:
    tunnel = await get_tunnel(user_id)
    if tunnel and tunnel.prune_ready():
        await delete_tunnel(user_id, tunnel.tunnel_id)
        return None
    return tunnel


async def _remote_create(days: int) -> TunnelRecord:
    payload = {"public_id": REMOTE_PUBLIC_ID, "days": days}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{REMOTE_BASE}/reverse_proxy/api/v1/tunnels", json=payload)
        resp.raise_for_status()
        data = resp.json()
    expires = datetime.now(timezone.utc)
    return TunnelRecord(id="", user_id="", status="pending", days=days, expires_at=expires, **data)


async def _remote_topup(tunnel_id: str, days: int) -> dict:
    amount = max(days * PRICE_PER_DAY, PRICE_PER_DAY)
    payload = {"tunnel_id": tunnel_id, "invoice_id": "", "amount": amount, "paid": False}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{REMOTE_BASE}/reverse_proxy/api/v1/payments/public/{tunnel_id}", json=payload
        )
        resp.raise_for_status()
        return resp.json()


async def create_or_topup(user_id: str, days: int) -> TunnelRecord:
    existing = await fetch_existing(user_id)
    if existing and not existing.prune_ready():
        pay = await _remote_topup(existing.tunnel_id, days)
        existing.payment_hash = pay.get("payment_hash", existing.payment_hash)
        existing.payment_request = pay.get("payment_request", existing.payment_request)
        existing.days = days
        existing.status = "pending"
        existing.updated_at = datetime.now(timezone.utc)
        await save_tunnel(user_id, existing)
        return existing

    new_tunnel = await _remote_create(days)
    new_tunnel.user_id = user_id
    new_tunnel.updated_at = datetime.now(timezone.utc)
    await save_tunnel(user_id, new_tunnel)
    return new_tunnel


async def activate_tunnel(user_id: str, payment_hash: str | None = None) -> TunnelRecord | None:
    tunnel = None
    if payment_hash:
        tunnel = await get_by_payment_hash(payment_hash)
    if not tunnel:
        tunnel = await fetch_existing(user_id)
    if not tunnel:
        return None

    key_path = _write_key(user_id, tunnel)
    _launch_ssh(tunnel, key_path)
    if tunnel.status != "active":
        tunnel.expires_at = max(tunnel.expires_at, datetime.now(timezone.utc)) + timedelta(days=tunnel.days)
    tunnel.status = "active"
    tunnel.updated_at = datetime.now(timezone.utc)
    await save_tunnel(user_id, tunnel)
    return tunnel


def _write_key(user_id: str, tunnel: TunnelRecord) -> str:
    key_dir = os.path.join(os.path.expanduser("~"), ".lnbits", "tunnel_me_out", user_id)
    os.makedirs(key_dir, exist_ok=True)
    key_path = os.path.join(key_dir, f"{tunnel.tunnel_id}.key")
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(tunnel.ssh_private_key)
    os.chmod(key_path, 0o600)
    return key_path


def _launch_ssh(tunnel: TunnelRecord, key_path: str) -> None:
    cmd = [
        "ssh",
        "-i",
        key_path,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-N",
        "-R",
        f"{tunnel.remote_port}:localhost:5000",
        f"{tunnel.ssh_user}@{tunnel.ssh_host}",
    ]
    try:
        subprocess.Popen(cmd)
        logger.info(f"tunnel_me_out: launched ssh for tunnel {tunnel.tunnel_id}")
    except Exception as exc:
        logger.error(f"tunnel_me_out: failed to launch ssh: {exc}")
