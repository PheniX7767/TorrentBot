from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.services.delivery import format_size
from bot.services.jobs import job_manager
from bot.services.pipeline import Pipeline, force_clean_downloads
from bot.services.qbittorrent import QBittorrentService
from bot.services.usage_stats import UsageStats


router = Router(name="commands")


def _uid(message: Message) -> int:
    assert message.from_user
    return message.from_user.id


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    lines = [
        "Привет! Я скачаю торрент или ссылку на видео/пост на VPS и пришлю файлы сюда.\n",
        "Пришлите:",
        "• <b>magnet</b>-ссылку или файл <code>.torrent</code>",
        "• ссылку на видео или пост (YouTube, Instagram, TikTok и другие)\n",
        "Команды:",
        "/status — текущая задача",
        "/cancel — отменить задачу",
        "/clean — очистить загрузки на диске",
    ]
    if message.from_user and message.from_user.id in settings.admin_ids:
        lines.append("/stats — статистика трафика")
    await message.answer("\n".join(lines))


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(job_manager.get(_uid(message)).summary())


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    settings: Settings,
    usage_stats: UsageStats,
) -> None:
    user_id = _uid(message)
    if user_id not in settings.admin_ids:
        await message.answer("Нет доступа.")
        return

    snapshot = await usage_stats.snapshot()
    names = settings.client_name_map
    rows: list[tuple[int, int, int, str]] = []
    for uid in settings.allowed_ids:
        counters = snapshot.get(uid)
        download = counters.download if counters else 0
        upload = counters.upload if counters else 0
        if uid in names:
            label = f"{names[uid]} (<code>{uid}</code>)"
        else:
            label = f"<code>{uid}</code>"
        rows.append((download + upload, download, upload, label))

    rows.sort(key=lambda r: r[0], reverse=True)

    lines = ["<b>Статистика трафика</b>", ""]
    total_dl = 0
    total_ul = 0
    for _total, download, upload, label in rows:
        total_dl += download
        total_ul += upload
        lines.append(
            f"{label}\n"
            f"  ↓ {format_size(download)}  ↑ {format_size(upload)}"
        )
    lines.append("")
    lines.append(
        f"<b>Итого</b>: ↓ {format_size(total_dl)}  ↑ {format_size(total_ul)}"
    )
    await message.answer("\n".join(lines))


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
