import asyncio

from fastapi import APIRouter
from lnbits.tasks import create_permanent_unique_task

from .crud import db
from .services import shutdown
from .tasks import rehydrate_and_activate
from .views import tunnel_router_frontend
from .views_api import tunnel_router

tunnel_me_out_ext: APIRouter = APIRouter(prefix="/tunnel_me_out", tags=["tunnel_me_out"])
tunnel_me_out_ext.include_router(tunnel_router_frontend)
tunnel_me_out_ext.include_router(tunnel_router)

tunnel_me_out_static_files = [
    {
        "path": "/tunnel_me_out/static",
        "name": "tunnel_me_out_static",
    }
]

scheduled_tasks: list[asyncio.Task] = []


def tunnel_me_out_start():
    task = create_permanent_unique_task("ext_tunnel_me_out", rehydrate_and_activate)
    scheduled_tasks.append(task)


async def tunnel_me_out_stop():
    for task in scheduled_tasks:
        task.cancel()
    await asyncio.gather(*scheduled_tasks, return_exceptions=True)
    scheduled_tasks.clear()
    await shutdown()


__all__ = [
    "db",
    "tunnel_me_out_ext",
    "tunnel_me_out_start",
    "tunnel_me_out_static_files",
    "tunnel_me_out_stop",
]
