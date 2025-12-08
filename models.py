from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field


class TunnelRequest(BaseModel):
    days: int = Field(gt=0)
    local_host: str | None = Field(default=None)
    local_port: int | None = Field(default=None, gt=0, lt=65536)


class TunnelRecord(BaseModel):
    id: str
    tunnel_id: str
    subdomain: str
    remote_port: int
    ssh_user: str
    ssh_host: str
    ssh_private_key: str
    ssh_command: str
    public_url: str
    expires_at: datetime
    payment_hash: str
    payment_request: str
    status: str
    days: int
    local_host: str = Field(default="localhost")
    local_port: int = Field(default=5000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def prune_ready(self) -> bool:
        return datetime.now(timezone.utc) - self.expires_at > timedelta(days=7)


class TunnelResponse(BaseModel):
    tunnel: TunnelRecord | None
