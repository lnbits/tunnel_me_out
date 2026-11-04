import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from lnbits.core.models import User

from .. import services, tasks, views_api
from ..models import TunnelRecord


@pytest.fixture
def tunnel():
    return TunnelRecord(
        id="owner",
        tunnel_id="tunnel",
        subdomain="test",
        remote_port=42000,
        ssh_user="tunneluser",
        ssh_host="example.com",
        ssh_private_key="test-private-key",
        ssh_command="",
        public_url="https://test.example.com",
        expires_at=datetime.now(timezone.utc),
        payment_hash="hash",
        payment_request="invoice",
        status="pending",
        days=2,
    )


@pytest.fixture
def state(tunnel):
    return {
        "tunnel_id": tunnel.tunnel_id,
        "paid": True,
        "ready": True,
        "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "invoice_expired": False,
    }


@pytest_asyncio.fixture(autouse=True)
async def isolate():
    # Each pytest async test owns its event loop.
    services._lifecycle_lock = asyncio.Lock()
    yield
    await services.shutdown()
    services._ssh_errors.clear()


@pytest.fixture
def launch_ssh(monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(services, "_launch_ssh", launch)
    return launch


@pytest.fixture
def local_store(tunnel, monkeypatch, launch_ssh):
    current = [tunnel.model_copy(deep=True)]

    async def get(user):
        return current[0].model_copy(deep=True) if current[0] and user == current[0].id else None

    async def save(user, record):
        assert user == record.id
        current[0] = record.model_copy(deep=True)
        return record

    monkeypatch.setattr(services, "get_tunnel", get)
    monkeypatch.setattr(services, "save_tunnel", save)
    monkeypatch.setattr(services, "_write_key", lambda *args: ("/tmp/test-key", "/tmp/known-hosts"))
    monkeypatch.setattr(services, "ensure_payment_listener", lambda *args: None)
    return current


@pytest.mark.asyncio
async def test_refresh_preserves_pending_invoice_and_key(local_store):
    now = datetime.now(timezone.utc)
    user = User(id="owner", created_at=now, updated_at=now, super_user=True)
    response = await views_api.get_tunnel(user=user)
    assert response.tunnel is not None
    assert response.tunnel.status == "pending"
    assert response.tunnel.payment_hash == "hash"
    assert response.tunnel.ssh_private_key == "test-private-key"
    assert local_store[0] is not None


@pytest.mark.asyncio
async def test_repeated_click_returns_pending_invoice(local_store, state, monkeypatch):
    state.update(paid=False, ready=False, status="pending")
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    topup = AsyncMock()
    monkeypatch.setattr(services, "_remote_topup", topup)
    first, second = await asyncio.gather(services.create_or_topup("owner", 2), services.create_or_topup("owner", 4))
    assert first.payment_hash == second.payment_hash == "hash"
    assert first.ssh_private_key == second.ssh_private_key == "test-private-key"
    topup.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_and_duplicate_confirmation_copy_server_expiry(local_store, state, monkeypatch):
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    first = await services.activate_tunnel("owner", "hash")
    second = await services.activate_tunnel("owner", "hash")
    third = await services.activate_tunnel("owner")
    assert first is not None
    assert second is not None
    assert third is not None
    assert first.expires_at == second.expires_at == third.expires_at
    assert third.expires_at.isoformat() == state["expires_at"]
    assert third.status == "active"


@pytest.mark.asyncio
async def test_stale_hash_or_other_user_cannot_activate(local_store, monkeypatch):
    remote = AsyncMock()
    monkeypatch.setattr(services, "_remote_status", remote)
    assert await services.activate_tunnel("owner", "old-hash") is None
    assert await services.activate_tunnel("other", "hash") is None
    remote.assert_not_awaited()


@pytest.mark.asyncio
async def test_unpaid_confirmation_cannot_activate(local_store, state, monkeypatch, launch_ssh):
    state.update(paid=False, ready=False, status="pending")
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    result = await services.activate_tunnel("owner", "hash")
    assert result is not None
    assert result.status == "pending"
    launch_ssh.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_renewal_keeps_existing_entitlement(local_store, state, monkeypatch, launch_ssh):
    state.update(paid=False, ready=False)
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    result = await services.activate_tunnel("owner")
    assert result is not None
    assert result.status == "pending"
    launch_ssh.assert_awaited_once()


@pytest.mark.asyncio
async def test_expiry_stops_ssh_even_if_server_unreachable(local_store, monkeypatch):
    local_store[0].status = "active"
    local_store[0].expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    stop = AsyncMock()
    monkeypatch.setattr(services, "_stop_ssh", stop)
    monkeypatch.setattr(services, "_remote_status", AsyncMock(side_effect=httpx.ConnectError("offline")))
    with pytest.raises(httpx.ConnectError):
        await services.activate_tunnel("owner")
    stop.assert_awaited_once_with("tunnel")


@pytest.mark.asyncio
async def test_launch_failure_is_saved_for_user(local_store, state, monkeypatch):
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    monkeypatch.setattr(
        services,
        "_launch_ssh",
        AsyncMock(side_effect=RuntimeError("Permission denied (publickey)")),
    )
    result = await services.activate_tunnel("owner")
    assert result is not None
    assert result.last_error is not None
    assert "Permission denied" in result.last_error
    assert local_store[0].last_error == result.last_error


@pytest.mark.asyncio
async def test_paid_invoice_recovers_without_websocket(local_store, state, monkeypatch):
    monkeypatch.setattr(services, "_remote_status", AsyncMock(return_value=state))
    await services._wait_for_payment("owner", "hash")
    assert local_store[0].status == "active"


@pytest.mark.asyncio
async def test_new_renewal_preserves_private_key(local_store, monkeypatch):
    local_store[0].status = "active"
    monkeypatch.setattr(
        services,
        "_remote_topup",
        AsyncMock(
            return_value={
                "payment_hash": "new-hash",
                "payment_request": "new-invoice",
            }
        ),
    )
    result = await services.create_or_topup("owner", 4)
    assert result.payment_hash == "new-hash"
    assert result.ssh_private_key == "test-private-key"
    assert result.tunnel_id == "tunnel"


@pytest.mark.asyncio
async def test_background_failure_does_not_skip_other_tunnels(tunnel, monkeypatch):
    tunnel.status = "active"
    other = tunnel.model_copy(update={"id": "other"})
    monkeypatch.setattr(tasks, "get_all_tunnels", AsyncMock(return_value=[tunnel, other]))
    activate = AsyncMock(side_effect=[RuntimeError("broken"), other])
    monkeypatch.setattr(tasks, "activate_tunnel", activate)
    await tasks.reconcile_tunnels_once()
    assert activate.await_count == 2


def test_ssh_command_isolates_identity_and_sets_loopback_and_keepalives(tunnel):
    tunnel.local_host = "::1"
    cmd = services._ssh_command(tunnel, "/tmp/key", "/tmp/path with spaces/known_hosts")
    assert "IdentitiesOnly=yes" in cmd
    assert "IdentityAgent=none" in cmd
    assert "BatchMode=yes" in cmd
    assert "ExitOnForwardFailure=yes" in cmd
    assert "ServerAliveInterval=15" in cmd
    assert "127.0.0.1:42000:[::1]:5000" in cmd
    # Let the installed OpenSSH parser validate the actual generated arguments.
    result = subprocess.run([cmd[0], "-G", *cmd[1:]], capture_output=True, text=True, check=True)
    assert "identityagent none" in result.stdout
    assert "/tmp/path with spaces/known_hosts" in result.stdout


def test_private_key_is_written_with_private_permissions(tunnel, tmp_path, monkeypatch):
    monkeypatch.setattr(services.os.path, "expanduser", lambda _: str(tmp_path))
    path, known_hosts = services._write_key(tunnel.id, tunnel)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
    assert not os.path.exists(known_hosts)  # SSH will populate it on first trust.
    tunnel.tunnel_id = "../escape"
    with pytest.raises(ValueError):
        services._write_key(tunnel.id, tunnel)


@pytest.mark.asyncio
async def test_healthy_ssh_is_not_restarted_and_shutdown_reaps(tunnel, monkeypatch):
    spawn = asyncio.create_subprocess_exec
    calls = []

    async def fake_ssh(*args, **kwargs):
        proc = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        calls.append(proc)
        return proc

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_ssh)
    await services._launch_ssh(tunnel, "/tmp/key", "/tmp/known")
    await services._launch_ssh(tunnel, "/tmp/key", "/tmp/known")
    assert len(calls) == 1
    await services.shutdown()
    assert calls[0].returncode is not None
    assert not services._ssh_processes


@pytest.mark.asyncio
async def test_ssh_startup_failure_is_reported(tunnel, monkeypatch):
    spawn = asyncio.create_subprocess_exec

    async def fake_ssh(*args, **kwargs):
        return await spawn(
            sys.executable, "-c", "import sys; sys.stderr.write('Permission denied'); sys.exit(255)", **kwargs
        )

    monkeypatch.setattr(services.asyncio, "create_subprocess_exec", fake_ssh)
    with pytest.raises(RuntimeError, match="Permission denied"):
        await services._launch_ssh(tunnel, "/tmp/key", "/tmp/known")
    assert not services._ssh_processes


@pytest.mark.asyncio
@pytest.mark.parametrize("tunnel_exists", [True, False])
async def test_missing_pending_status_recovers_without_unnecessary_key_replacement(
    local_store, monkeypatch, tunnel_exists
):
    request = httpx.Request("GET", "https://example.com/status")
    missing = httpx.HTTPStatusError("missing", request=request, response=httpx.Response(404, request=request))
    monkeypatch.setattr(services, "_remote_status", AsyncMock(side_effect=missing))
    topup = AsyncMock(return_value={"payment_hash": "new-hash", "payment_request": "new-invoice"})
    if not tunnel_exists:
        topup.side_effect = missing
    monkeypatch.setattr(services, "_remote_topup", topup)
    replacement = local_store[0].model_copy(update={"tunnel_id": "replacement", "ssh_private_key": "new-key"})
    create = AsyncMock(return_value=replacement)
    delete = AsyncMock()
    monkeypatch.setattr(services, "_remote_create", create)
    monkeypatch.setattr(services, "delete_tunnel", delete)
    result = await services.create_or_topup("owner", 3)
    topup.assert_awaited_once_with("tunnel", 3)
    if tunnel_exists:
        assert result.tunnel_id == "tunnel"
        assert result.ssh_private_key == "test-private-key"
        assert result.payment_hash == "new-hash"
        create.assert_not_awaited()
        delete.assert_not_awaited()
    else:
        assert result.tunnel_id == "replacement"
        create.assert_awaited_once_with("owner", 3, None, None)
        delete.assert_awaited_once_with("owner", "tunnel")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500])
async def test_pending_status_failure_preserves_reservation(local_store, monkeypatch, status):
    request = httpx.Request("GET", "https://example.com/status")
    error = httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status, request=request))
    monkeypatch.setattr(services, "_remote_status", AsyncMock(side_effect=error))
    topup = AsyncMock()
    monkeypatch.setattr(services, "_remote_topup", topup)
    with pytest.raises(httpx.HTTPStatusError):
        await services.create_or_topup("owner", 3)
    topup.assert_not_awaited()
    assert local_store[0].ssh_private_key == "test-private-key"
