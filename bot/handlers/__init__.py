from __future__ import annotations

from aiogram import Router

from bot.handlers import callbacks, commands, torrents, youtube


def setup_routers() -> Router:
    root = Router()
    root.include_router(commands.router)
    root.include_router(youtube.router)
    root.include_router(torrents.router)
    root.include_router(callbacks.router)
    return root
