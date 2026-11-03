import asyncio
import hashlib
import hmac
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone

import httpx
from lnbits.settings import settings
from loguru import logger

from .crud import delete_tunnel, get_tunnel, save_tunnel
from .models import TunnelRecord

REMOTE_BASE = os.getenv("TUNNEL_ME_OUT_REMOTE_BASE", "https://lnbits.lnpro.xyz").rstrip("/")
REMOTE_PUBLIC_ID = os.getenv("TUNNEL_ME_OUT_PUBLIC_ID", "aE4CBGPeRqcJufpWDVh53G")
PING_TIMEOUT = 8.0

_payment_watchers: dict[str, asyncio.Task] = {}
_ssh_processes: dict[str, asyncio.subprocess.Process] = {}
_ssh_commands: dict[str, list[str]] = {}
_ssh_readers: dict[str, asyncio.Task] = {}
_ssh_errors: dict[str, str] = {}
_lifecycle_lock = asyncio.Lock()


def _hash_client_note(user_id: str) -> str:
    secret = settings.auth_secret_key or ""
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()


def _cancel_payment_listener(payment_hash: str | None) -> None:
    if not payment_hash:
        return
    task = _payment_watchers.pop(payment_hash, None)
    if task and task is not asyncio.current_task():
        task.cancel()


async def fetch_existing(user_id: str) -> TunnelRecord | None:
    # GET is read-only. Pending invoices and their private keys must survive refresh.
    return await get_tunnel(user_id)


def _default_local_binding() -> tuple[str, int]:
    host = settings.host or "localhost"
    if host in {"0.0.0.0", "::"}:
        host = "localhost"
    return host, settings.port or 5000


def _apply_local_binding(
    tunnel: TunnelRecord,
    override_host: str | None = None,
    override_port: int | None = None,
) -> TunnelRecord:
    host, port = _default_local_binding()
    tunnel.local_host = override_host or tunnel.local_host or host
    tunnel.local_port = override_port or tunnel.local_port or port
    return tunnel


async def _remote_create(user_id: str, days: int, local_host: str | None, local_port: int | None) -> TunnelRecord:
    host, port = _default_local_binding()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{REMOTE_BASE}/reverse_proxy/api/v1/tunnels",
            json={
                "public_id": REMOTE_PUBLIC_ID,
                "days": days,
                "client_note": _hash_client_note(user_id),
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return TunnelRecord(
        id=user_id,
        status="pending",
        days=days,
        local_host=local_host or host,
        local_port=local_port or port,
        **data,
    )


async def _remote_topup(tunnel_id: str, days: int) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{REMOTE_BASE}/reverse_proxy/api/v1/payments/public/{tunnel_id}",
            json={"days": days},
        )
        resp.raise_for_status()
        return resp.json()


async def _remote_status(tunnel: TunnelRecord) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{REMOTE_BASE}/reverse_proxy/api/v1/tunnels/{tunnel.tunnel_id}/status",
            params={"payment_hash": tunnel.payment_hash},
        )
        resp.raise_for_status()
        data = resp.json()
    if data.get("tunnel_id") != tunnel.tunnel_id:
        raise ValueError("Server returned a different tunnel.")
    return data


async def create_or_topup(
    user_id: str,
    days: int,
    local_host: str | None = None,
    local_port: int | None = None,
) -> TunnelRecord:
    async with _lifecycle_lock:
        existing = await get_tunnel(user_id)
        if existing and existing.status == "pending":
            state = await _remote_status(existing)
            # Repeated clicks return the same unpaid invoice and key. Settled
            # invoices must be reconciled before a new payment replaces the hash.
            if not state.get("invoice_expired") or state.get("paid"):
                await _reconcile(existing, state)
                ensure_payment_listener(user_id, existing.payment_hash)
                return existing
        if existing:
            try:
                pay = await _remote_topup(existing.tunnel_id, days)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                await _stop_ssh(existing.tunnel_id)
                _cancel_payment_listener(existing.payment_hash)
                await delete_tunnel(user_id, existing.tunnel_id)
                existing = None
        if existing:
            _cancel_payment_listener(existing.payment_hash)
            existing.payment_hash = pay["payment_hash"]
            existing.payment_request = pay["payment_request"]
            existing.days = days
            existing.status = "pending"
            _apply_local_binding(existing, local_host, local_port)
            tunnel = existing
        else:
            tunnel = await _remote_create(user_id, days, local_host, local_port)
        tunnel.updated_at = datetime.now(timezone.utc)
        await save_tunnel(user_id, tunnel)
        ensure_payment_listener(user_id, tunnel.payment_hash)
        return tunnel


async def _reconcile(tunnel: TunnelRecord, state: dict) -> TunnelRecord:
    # The server is the only source of paid time. Reconnects and duplicate
    # confirmations must never extend a local expiry.
    expires = datetime.fromisoformat(state["expires_at"].replace("Z", "+00:00"))
    tunnel.expires_at = expires
    entitled = state.get("status") == "active" and not tunnel.is_expired()
    if state.get("paid") and state.get("ready") and entitled:
        tunnel.status = "active"
    elif state.get("paid") and not entitled:
        tunnel.status = "expired"
    if not entitled:
        await _stop_ssh(tunnel.tunnel_id)
    else:
        key_path, known_hosts_path = _write_key(tunnel.id, tunnel)
        tunnel.ssh_command = _build_script(tunnel, key_path, known_hosts_path)
        try:
            await _launch_ssh(tunnel, key_path, known_hosts_path)
            tunnel.last_error = None
        except Exception as exc:
            tunnel.last_error = str(exc)
            logger.warning(f"tunnel_me_out: tunnel {tunnel.tunnel_id}: {exc}")
    tunnel.updated_at = datetime.now(timezone.utc)
    await save_tunnel(tunnel.id, tunnel)
    return tunnel


async def activate_tunnel(user_id: str, payment_hash: str | None = None) -> TunnelRecord | None:
    async with _lifecycle_lock:
        tunnel = await get_tunnel(user_id)
        if not tunnel or (payment_hash is not None and payment_hash != tunnel.payment_hash):
            return None
        # A lost control-plane connection must not keep an expired SSH session alive.
        if tunnel.is_expired():
            await _stop_ssh(tunnel.tunnel_id)
        try:
            state = await _remote_status(tunnel)
        except Exception as exc:
            tunnel.last_error = f"Could not verify tunnel with server: {exc}"
            if tunnel.is_expired() and tunnel.status != "pending":
                tunnel.status = "expired"
            await save_tunnel(user_id, tunnel)
            raise
        return await _reconcile(tunnel, state)


async def ping_tunnel(user_id: str) -> bool:
    tunnel = await fetch_existing(user_id)
    if not tunnel or not tunnel.public_url or tunnel.is_expired():
        return False
    try:
        async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
            resp = await client.get(tunnel.public_url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


def _write_key(user_id: str, tunnel: TunnelRecord) -> tuple[str, str]:
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (user_id, tunnel.tunnel_id)):
        raise ValueError("Invalid tunnel identifier.")
    key_dir = os.path.join(os.path.expanduser("~"), ".lnbits", "tunnel_me_out", user_id)
    os.makedirs(key_dir, mode=0o700, exist_ok=True)
    os.chmod(key_dir, 0o700)
    key_path = os.path.join(key_dir, f"{tunnel.tunnel_id}.key")
    fd, temporary = tempfile.mkstemp(dir=key_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(tunnel.ssh_private_key.rstrip() + "\n")
        os.replace(temporary, key_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return key_path, os.path.join(key_dir, "known_hosts")


async def _stop_ssh(tunnel_id: str) -> None:
    proc = _ssh_processes.pop(tunnel_id, None)
    _ssh_commands.pop(tunnel_id, None)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
    reader = _ssh_readers.pop(tunnel_id, None)
    if reader:
        await reader


def _ssh_command(tunnel: TunnelRecord, key_path: str, known_hosts_path: str) -> list[str]:
    host = tunnel.local_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-i",
        key_path,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "BatchMode=yes",
        "-o",
        f'UserKnownHostsFile="{known_hosts_path}"',
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-N",
        "-T",
        "-R",
        f"127.0.0.1:{tunnel.remote_port}:{host}:{tunnel.local_port}",
        f"{tunnel.ssh_user}@{tunnel.ssh_host}",
    ]


async def _read_ssh_errors(tunnel_id: str, proc: asyncio.subprocess.Process) -> None:
    if proc.stderr:
        async for line in proc.stderr:
            message = line.decode(errors="replace").strip()[:2000]
            _ssh_errors[tunnel_id] = message
            logger.warning(f"tunnel_me_out: ssh {tunnel_id}: {message}")


async def _launch_ssh(tunnel: TunnelRecord, key_path: str, known_hosts_path: str) -> None:
    cmd = _ssh_command(tunnel, key_path, known_hosts_path)
    existing = _ssh_processes.get(tunnel.tunnel_id)
    if existing and existing.returncode is None and _ssh_commands.get(tunnel.tunnel_id) == cmd:
        return
    await _stop_ssh(tunnel.tunnel_id)
    _ssh_errors.pop(tunnel.tunnel_id, None)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _ssh_processes[tunnel.tunnel_id] = proc
    _ssh_commands[tunnel.tunnel_id] = cmd
    _ssh_readers[tunnel.tunnel_id] = asyncio.create_task(_read_ssh_errors(tunnel.tunnel_id, proc))
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except asyncio.TimeoutError:
        return  # Running; public reachability is checked separately, not assumed.
    await _stop_ssh(tunnel.tunnel_id)
    raise RuntimeError(_ssh_errors.get(tunnel.tunnel_id) or f"SSH exited with status {proc.returncode}")


def _build_script(tunnel: TunnelRecord, key_path: str, known_hosts_path: str) -> str:
    return "\n".join(
        [
            "umask 077",
            f"mkdir -p {shlex.quote(os.path.dirname(key_path))}",
            f"cat > {shlex.quote(key_path)} <<'TUNNEL_ME_OUT_KEY'",
            tunnel.ssh_private_key.strip(),
            "TUNNEL_ME_OUT_KEY",
            f"chmod 600 {shlex.quote(key_path)}",
            shlex.join(_ssh_command(tunnel, key_path, known_hosts_path)),
        ]
    )


def ensure_payment_listener(user_id: str, payment_hash: str | None) -> None:
    if not payment_hash:
        return
    task = _payment_watchers.get(payment_hash)
    if task and not task.done():
        return
    _payment_watchers[payment_hash] = asyncio.create_task(_wait_for_payment(user_id, payment_hash))


async def _wait_for_payment(user_id: str, payment_hash: str) -> None:
    try:
        while True:
            try:
                tunnel = await activate_tunnel(user_id, payment_hash)
                if not tunnel or tunnel.status != "pending":
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"tunnel_me_out: payment reconciliation failed: {exc}")
            await asyncio.sleep(5)
    finally:
        if _payment_watchers.get(payment_hash) is asyncio.current_task():
            _payment_watchers.pop(payment_hash, None)


async def shutdown() -> None:
    watchers = list(_payment_watchers.values())
    for task in watchers:
        task.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)
    for tunnel_id in list(_ssh_processes):
        await _stop_ssh(tunnel_id)
    _payment_watchers.clear()
    _ssh_errors.clear()
