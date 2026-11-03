import asyncio

from loguru import logger

from .crud import get_all_tunnels
from .services import activate_tunnel, ensure_payment_listener


async def reconcile_tunnels_once():
    for tunnel in await get_all_tunnels():
        try:
            if tunnel.status == "pending":
                ensure_payment_listener(tunnel.id, tunnel.payment_hash)
            else:
                await activate_tunnel(tunnel.id)
        except Exception as exc:
            # One unavailable server or broken key must not skip other tunnels.
            logger.warning(f"tunnel_me_out: reconciliation failed for {tunnel.tunnel_id}: {exc}")


async def rehydrate_and_activate():
    while True:
        try:
            await reconcile_tunnels_once()
        except Exception as exc:
            logger.warning(f"tunnel_me_out: reconciliation failed: {exc}")
        await asyncio.sleep(30)
