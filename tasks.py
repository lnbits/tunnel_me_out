import asyncio
import sqlite3

from loguru import logger

from .crud import get_all_tunnels
from .services import activate_tunnel, ensure_payment_listener


async def rehydrate_and_activate():
    while True:
        try:
            tunnels = await get_all_tunnels()
            for t in tunnels:
                if t.status == "pending":
                    ensure_payment_listener(t.id, t.payment_hash)
                    continue
                if t.status == "active":
                    await activate_tunnel(t.id)
        except sqlite3.OperationalError as exc:
            if "no such table: tunnel_me_out.tunnels" in str(exc):
                # migrations not applied yet; skip quietly until next run
                await asyncio.sleep(300)
                continue
            logger.error(f"tunnel_me_out: db error {exc}")
        except Exception as exc:
            logger.error(f"tunnel_me_out: rehydrate error {exc}")
        await asyncio.sleep(300)
