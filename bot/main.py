from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from bot.config import get_settings
from bot.handlers import setup_routers
from bot.middlewares.acl import AllowlistMiddleware
from bot.services.pipeline import Pipeline, ensure_writable_dir
from bot.services.qbittorrent import QBittorrentService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot")


async def wait_qbit(qbit: QBittorrentService, attempts: int = 60) -> None:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            await asyncio.to_thread(qbit.connect)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning("qBittorrent not ready (%s/%s): %s", i + 1, attempts, exc)
            await asyncio.sleep(2)
    raise RuntimeError(f"Cannot connect to qBittorrent: {last_exc}")


async def main() -> None:
    try:
        settings = get_settings()
    except Exception:
        logger.exception(
            "Не удалось загрузить настройки. Проверьте .env: "
            "BOT_TOKEN, ALLOWED_USER_IDS (или ALLOWED_USER_ID), QBITTORRENT_PASSWORD"
        )
        raise
    ensure_writable_dir(
        Path(settings.downloads_dir),
        settings.puid,
        settings.pgid,
    )

    session = AiohttpSession(timeout=3600)
    bot = Bot(
        token=settings.bot_token,
        session=session,
        base_url=settings.bot_api_url,
        base_file_url=settings.bot_api_file_url,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    qbit = QBittorrentService(settings)
    await wait_qbit(qbit)

    pipeline = Pipeline(bot=bot, settings=settings, qbit=qbit)
    pipeline.start_background()

    dp = Dispatcher()

    acl = AllowlistMiddleware(settings)
    # Register on dispatcher so ACL wraps all routers reliably
    dp.message.middleware(acl)
    dp.callback_query.middleware(acl)
    dp.include_router(setup_routers())

    logger.info(
        "Allowlist user ids: %s",
        ", ".join(str(i) for i in sorted(settings.allowed_ids)),
    )
    logger.info("Starting polling via local Bot API %s", settings.bot_api_base_url)
    try:
        await dp.start_polling(
            bot,
            settings=settings,
            qbit=qbit,
            pipeline=pipeline,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await pipeline.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
