from lnbits.db import Database
from lnbits.helpers import urlsafe_short_hash

from .models import TunnelRecord

db = Database("ext_tunnel_me_out")


async def get_tunnel(user_id: str) -> TunnelRecord | None:
    return await db.fetchone(
        """
            SELECT * FROM tunnel_me_out.tunnels
            WHERE id = :user_id
            ORDER BY created_at DESC
            LIMIT 1
        """,
        {"user_id": user_id},
        TunnelRecord,
    )


async def get_all_tunnels() -> list[TunnelRecord]:
    rows = await db.fetchall(
        """
            SELECT * FROM tunnel_me_out.tunnels
        """,
        model=TunnelRecord,
    )
    return rows


async def save_tunnel(user_id: str, data: TunnelRecord) -> TunnelRecord:
    record = data
    if not getattr(data, "id", None):
        payload = data.dict(exclude={"id"})
        record = TunnelRecord(**payload, id=user_id or urlsafe_short_hash())

    existing = await get_tunnel(user_id)
    if existing:
        # Preserve original creation time when updating.
        record.created_at = existing.created_at
        await db.update("tunnel_me_out.tunnels", record, where="WHERE id = :id")
    else:
        await db.insert("tunnel_me_out.tunnels", record)
    return record


async def delete_tunnel(user_id: str, tunnel_id: str | None = None) -> None:
    if tunnel_id:
        await db.execute(
            """
                DELETE FROM tunnel_me_out.tunnels
                WHERE id = :user_id AND tunnel_id = :tunnel_id
            """,
            {"user_id": user_id, "tunnel_id": tunnel_id},
        )
    else:
        await db.execute(
            """
                DELETE FROM tunnel_me_out.tunnels
                WHERE id = :user_id
            """,
            {"user_id": user_id},
        )


async def get_by_payment_hash(payment_hash: str) -> TunnelRecord | None:
    return await db.fetchone(
        """
            SELECT * FROM tunnel_me_out.tunnels
            WHERE payment_hash = :payment_hash
        """,
        {"payment_hash": payment_hash},
        TunnelRecord,
    )
