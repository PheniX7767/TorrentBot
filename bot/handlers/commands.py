from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.services.jobs import job_manager
from bot.services.pipeline import Pipeline, force_clean_downloads
from bot.services.qbittorrent import QBittorrentService


router = Router(name="commands")


def _uid(message: Message) -> int:
    assert message.from_user
    return message.from_user.id


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я скачаю торрент или видео с YouTube на VPS и пришлю файлы сюда.\n\n"
        "Пришлите:\n"
        "• <b>magnet</b>-ссылку или файл <code>.torrent</code>\n"
        "• ссылку на <b>YouTube</b>\n\n"
        "Команды:\n"
        "/status — текущая задача\n"
        "/cancel — отменить задачу\n"
        "/clean — очистить загрузки на диске",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(job_manager.get(_uid(message)).summary())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, pipeline: Pipeline) -> None:
    user_id = _uid(message)
    job = job_manager.get(user_id)
    if not job.is_active() and job.state.value == "idle":
        await message.answer("Нечего отменять.")
        return
    await pipeline.request_cancel(user_id)
    await message.answer("Задача отменена, данные очищены.")


@router.message(Command("clean"))
async def cmd_clean(
    message: Message,
    settings: Settings,
    qbit: QBittorrentService,
    pipeline: Pipeline,
) -> None:
    user_id = _uid(message)
    if job_manager.get(user_id).is_active():
        await pipeline.request_cancel(user_id)
    # Cancel other users' active jobs before wiping shared disk
    for job in list(job_manager.active_jobs()):
        if job.user_id is not None and job.user_id != user_id:
            await pipeline.request_cancel(job.user_id)
    removed = await force_clean_downloads(settings, qbit)
    await message.answer(f"Очистка завершена. Удалено объектов: {removed}.")
