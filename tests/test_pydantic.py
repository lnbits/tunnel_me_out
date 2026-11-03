import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from lnbits.core.models import User

from .. import crud, views
from ..models import TunnelRecord, TunnelResponse


@pytest.fixture
def old_record():
    return {
        "id": "",
        "tunnel_id": "tunnel",
        "subdomain": "example",
        "remote_port": 12000,
        "ssh_user": "tunneluser",
        "ssh_host": "example.com",
        "ssh_private_key": "test-key",
        "ssh_command": "ssh",
        "public_url": "https://example.com",
        "expires_at": "2030-01-01T00:00:00Z",
        "payment_hash": "hash",
        "payment_request": "invoice",
        "status": "pending",
        "days": 1,
    }


def test_old_records_and_empty_response_keep_optional_defaults(old_record):
    tunnel = TunnelRecord.model_validate(old_record)
    assert tunnel.last_error is None
    assert tunnel.local_host == "localhost"
    assert tunnel.expires_at.tzinfo == timezone.utc
    assert TunnelRecord.model_validate_json(tunnel.model_dump_json()) == tunnel
    assert TunnelResponse().model_dump() == {"tunnel": None}


@pytest.mark.asyncio
async def test_save_assigns_owner_and_preserves_creation_time(old_record, monkeypatch):
    insert, update = AsyncMock(), AsyncMock()
    monkeypatch.setattr(crud, "db", SimpleNamespace(insert=insert, update=update))
    monkeypatch.setattr(crud, "get_tunnel", AsyncMock(return_value=None))
    saved = await crud.save_tunnel("owner", TunnelRecord.model_validate(old_record))
    assert saved.id == "owner"
    assert isinstance(saved.expires_at, datetime)
    assert insert.await_args.args[1] is saved
    monkeypatch.setattr(crud, "get_tunnel", AsyncMock(return_value=saved))
    edited = TunnelRecord.model_validate({**old_record, "last_error": "Connection refused"})
    updated = await crud.save_tunnel("owner", edited)
    assert updated.created_at == saved.created_at
    assert updated.last_error == "Connection refused"
    assert update.await_args.args[1] is updated


@pytest.mark.asyncio
async def test_frontend_serializes_user_for_template(monkeypatch):
    now = datetime.now(timezone.utc)
    user = User(id="owner", created_at=now, updated_at=now, super_user=True)
    request = Request({"type": "http", "method": "GET", "path": "/"})
    renderer = SimpleNamespace(TemplateResponse=lambda name, context: context)
    monkeypatch.setattr(views, "tunnel_renderer", lambda: renderer)
    context = await views.index(request, user)
    assert json.loads(context["user"])["id"] == user.id
    assert User.model_validate_json(context["user"]).created_at == now
