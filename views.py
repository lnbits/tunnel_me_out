from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

tunnel_router_frontend = APIRouter()


def tunnel_renderer():
    return template_renderer(["tunnel_me_out/templates"])


@tunnel_router_frontend.get("/", response_class=HTMLResponse)
async def index(req: Request, user: User = Depends(check_user_exists)):
    if not user.super_user:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="User not authorized. No super user privileges.",
        )

    return tunnel_renderer().TemplateResponse("tunnel_me_out/index.html", {"request": req, "user": user.json()})
