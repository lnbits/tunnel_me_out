from fastapi import APIRouter, Depends, HTTPException
from lnbits.core.models import User
from lnbits.decorators import check_super_user
from .models import TunnelRequest, TunnelResponse, TunnelRecord
from .services import activate_tunnel, create_or_topup, fetch_existing

tunnel_router = APIRouter()


@tunnel_router.get("/api/v1/tunnel", response_model=TunnelResponse)
async def get_tunnel(user: User = Depends(check_super_user)) -> TunnelResponse:
    tunnel = await fetch_existing(user.id)
    return TunnelResponse(tunnel=tunnel)


@tunnel_router.post("/api/v1/tunnel", response_model=TunnelRecord)
async def create_tunnel(req: TunnelRequest, user: User = Depends(check_super_user)) -> TunnelRecord:
    return await create_or_topup(user.id, req.days)


@tunnel_router.post("/api/v1/tunnel/confirm", response_model=TunnelRecord)
async def confirm_tunnel(payment_hash: str, user: User = Depends(check_super_user)) -> TunnelRecord:
    tunnel = await activate_tunnel(user.id, payment_hash)
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    return tunnel
