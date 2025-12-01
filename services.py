import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
import websockets
from lnbits.settings import settings
from loguru import logger

from .crud import delete_tunnel, get_by_payment_hash, get_tunnel, save_tunnel
from .models import TunnelRecord

REMOTE_BASE = "https://lnbits.lnpro.xyz"
REMOTE_PUBLIC_ID = "aE4CBGPeRqcJufpWDVh53G"
REMOTE_WS_BASE = "wss://lnbits.lnpro.xyz/api/v1/ws"
PING_TIMEOUT = 8.0

_payment_watchers: dict[str, asyncio.Task] = {}
_ssh_processes: dict[str, subprocess.Popen] = {}


def _cancel_payment_listener(payment_hash: str | None) -> None:
    if not payment_hash:
        return
    task = _payment_watchers.pop(payment_hash, None)
    if task:
        task.cancel()


async def fetch_existing(user_id: str, *, prune_pending: bool = False) -> TunnelRecord | None:
    tunnel = await get_tunnel(user_id)
    if tunnel and tunnel.prune_ready():
        _cancel_payment_listener(tunnel.payment_hash)
        await delete_tunnel(user_id, tunnel.tunnel_id)
        return None
    if tunnel and prune_pending and tunnel.status == "pending":
        _cancel_payment_listener(tunnel.payment_hash)
        await delete_tunnel(user_id, tunnel.tunnel_id)
        return None
    return tunnel


def _default_local_binding() -> tuple[str, int]:
    host = settings.host or "localhost"
    # if listening on all interfaces, connect to localhost for the reverse tunnel
    if host in {"0.0.0.0", "::"}:
        host = "localhost"
    port = settings.port or 5000
    return host, port


def _apply_local_binding(tunnel: TunnelRecord) -> TunnelRecord:
    host, port = _default_local_binding()
    tunnel.local_host = host
    tunnel.local_port = port
    return tunnel


async def _remote_create(user_id: str, days: int) -> TunnelRecord:
    payload = {"public_id": REMOTE_PUBLIC_ID, "days": days, "client_note": user_id}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{REMOTE_BASE}/reverse_proxy/api/v1/tunnels", json=payload)
        resp.raise_for_status()
        data = resp.json()
    expires = data.get("expires_at") or datetime.now(timezone.utc).isoformat()
    data["expires_at"] = expires
    host, port = _default_local_binding()
    return TunnelRecord(
        id="",
        status="pending",
        days=days,
        local_host=host,
        local_port=port,
        **data,
    )


async def _remote_topup(tunnel_id: str, days: int) -> dict:
    payload = {"tunnel_id": tunnel_id, "days": days}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(f"{REMOTE_BASE}/reverse_proxy/api/v1/payments/public/{tunnel_id}", json=payload)
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
        _apply_local_binding(existing)
        await save_tunnel(user_id, existing)
        ensure_payment_listener(user_id, existing.payment_hash)
        return existing

    new_tunnel = await _remote_create(user_id, days)
    new_tunnel.id = user_id
    _apply_local_binding(new_tunnel)
    new_tunnel.updated_at = datetime.now(timezone.utc)
    await save_tunnel(user_id, new_tunnel)
    ensure_payment_listener(user_id, new_tunnel.payment_hash)
    return new_tunnel


async def activate_tunnel(user_id: str, payment_hash: str | None = None) -> TunnelRecord | None:
    tunnel = None
    if payment_hash:
        tunnel = await get_by_payment_hash(payment_hash)
    if not tunnel:
        tunnel = await fetch_existing(user_id)
    if not tunnel:
        return None

    _apply_local_binding(tunnel)
    key_path, known_hosts_path = _write_key(user_id, tunnel)
    _launch_ssh(tunnel, key_path, known_hosts_path)
    tunnel.ssh_command = _build_script(tunnel, key_path, known_hosts_path)
    should_extend = payment_hash is not None or tunnel.status == "pending"
    if should_extend:
        tunnel.expires_at = max(tunnel.expires_at, datetime.now(timezone.utc)) + timedelta(days=tunnel.days)
    tunnel.status = "active"
    tunnel.updated_at = datetime.now(timezone.utc)
    await save_tunnel(user_id, tunnel)
    return tunnel


async def ping_tunnel(user_id: str) -> bool:
    tunnel = await fetch_existing(user_id)
    if not tunnel or not tunnel.public_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
            resp = await client.get(tunnel.public_url)
            return resp.status_code < 500
    except Exception as exc:
        logger.info(f"tunnel_me_out: ping failed for {tunnel.public_url}: {exc}")
        return False


def _write_key(user_id: str, tunnel: TunnelRecord) -> tuple[str, str]:
    key_dir = os.path.join(os.path.expanduser("~"), ".lnbits", "tunnel_me_out", user_id)
    os.makedirs(key_dir, exist_ok=True)
    key_path = os.path.join(key_dir, f"{tunnel.tunnel_id}.key")
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(tunnel.ssh_private_key)
    os.chmod(key_path, 0o600)
    known_hosts_path = os.path.join(key_dir, "known_hosts")
    if not os.path.exists(known_hosts_path):
        with open(known_hosts_path, "a", encoding="utf-8"):
            os.utime(known_hosts_path, None)
    return key_path, known_hosts_path


def _launch_ssh(tunnel: TunnelRecord, key_path: str, known_hosts_path: str) -> None:
    existing_proc = _ssh_processes.get(tunnel.tunnel_id)
    if existing_proc and existing_proc.poll() is None:
        logger.info(f"tunnel_me_out: ssh already running for tunnel {tunnel.tunnel_id}")
        return
    if existing_proc and existing_proc.poll() is not None:
        _ssh_processes.pop(tunnel.tunnel_id, None)

    cmd = [
        "ssh",
        "-i",
        key_path,
        "-o",
        f"UserKnownHostsFile={known_hosts_path}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        "-R",
        f"{tunnel.remote_port}:{tunnel.local_host}:{tunnel.local_port}",
        f"{tunnel.ssh_user}@{tunnel.ssh_host}",
    ]
    try:
        proc = subprocess.Popen(cmd)
        _ssh_processes[tunnel.tunnel_id] = proc
        logger.info(f"tunnel_me_out: launched ssh for tunnel {tunnel.tunnel_id}")
    except Exception as exc:
        logger.error(f"tunnel_me_out: failed to launch ssh: {exc}")


def _build_script(tunnel: TunnelRecord, key_path: str, known_hosts_path: str) -> str:
    return "\n".join(
        [
            f"cat > {key_path} <<'EOF'",
            tunnel.ssh_private_key.strip(),
            "EOF",
            f"chmod 600 {key_path}",
            (
                "ssh "
                f"-i {key_path} "
                f"-o UserKnownHostsFile={known_hosts_path} "
                "-o StrictHostKeyChecking=accept-new "
                f"-N -R {tunnel.remote_port}:{tunnel.local_host}:{tunnel.local_port} "
                f"{tunnel.ssh_user}@{tunnel.ssh_host}"
            ),
        ]
    )


def ensure_payment_listener(user_id: str, payment_hash: str | None) -> None:
    if not payment_hash:
        return
    if payment_hash in _payment_watchers:
        return
    loop = asyncio.get_event_loop()
    _payment_watchers[payment_hash] = loop.create_task(_wait_for_payment(user_id, payment_hash))


async def _wait_for_payment(user_id: str, payment_hash: str) -> None:
    url = f"{REMOTE_WS_BASE}/{payment_hash}"
    try:
        while True:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        try:
                            payload = json.loads(msg)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"tunnel_me_out: failed to parse payment payload for {payment_hash}: {msg!s}"
                            )
                            continue
                        if payload.get("status") == "success" or payload.get("paid"):
                            await activate_tunnel(user_id, payment_hash)
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"tunnel_me_out: websocket error for {payment_hash}: {exc}")
            await asyncio.sleep(5)
    finally:
        _payment_watchers.pop(payment_hash, None)
