from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.config import Settings
from bot.keyboards.files import FILES_PER_PAGE, files_keyboard, picker_text
from bot.services.delivery import ensure_limits, format_size
from bot.services.jobs import JobState, job_manager
from bot.services.pipeline import Pipeline
from bot.services.ytdlp import FORMAT_MAP


router = Router(name="callbacks")


def _uid(query: CallbackQuery) -> int:
    assert query.from_user
    return query.from_user.id


@router.callback_query(F.data == "f:noop")
async def on_noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data.startswith("yt:"))
async def on_quality_picker(
    query: CallbackQuery,
    pipeline: Pipeline,
) -> None:
    user_id = _uid(query)
    job = job_manager.get(user_id)
    if job.kind != "ytdlp" or job.state != JobState.SELECTING:
        await query.answer("Сейчас выбор качества недоступен", show_alert=True)
        return
    if query.message and job.picker_message_id and query.message.message_id != job.picker_message_id:
        await query.answer("Это устаревшее меню", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        await pipeline.request_cancel(user_id)
        if query.message:
            await query.message.edit_text("Отменено.")
        await query.answer()
        return

    if action == "go" and len(parts) == 3:
        fmt = parts[2]
        if fmt not in FORMAT_MAP:
            await query.answer("Неизвестный формат", show_alert=True)
            return
        job.media_format = fmt
        job.touch()
        label = {
            "best": "лучшее",
            "1080": "1080p",
            "720": "720p",
            "480": "480p",
            "audio": "аудио mp3",
        }.get(fmt, fmt)
        if query.message:
            await query.message.edit_text(
                f"Скачиваю ({label}):\n{job.torrent_name}"
            )
        await query.answer("Старт")
        await pipeline.start_ytdlp_download(user_id)
        return

    await query.answer()


@router.callback_query(F.data.startswith("f:"))
async def on_file_picker(
    query: CallbackQuery,
    settings: Settings,
    pipeline: Pipeline,
) -> None:
    user_id = _uid(query)
    job = job_manager.get(user_id)
    if job.kind != "torrent" or job.state != JobState.SELECTING:
        await query.answer("Сейчас выбор файлов недоступен", show_alert=True)
        return
    if query.message and job.picker_message_id and query.message.message_id != job.picker_message_id:
        await query.answer("Это устаревшее меню", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "tog" and len(parts) == 3:
        idx = int(parts[2])
        if idx in job.selected:
            job.selected.discard(idx)
        else:
            job.selected.add(idx)
        job.touch()
        await _refresh_picker(query, job)
        await query.answer()
        return

    if action == "page" and len(parts) == 3:
        page = int(parts[2])
        total_pages = max(1, (len(job.files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
        job.page = max(0, min(page, total_pages - 1))
        job.touch()
        await _refresh_picker(query, job)
        await query.answer()
        return

    if action == "all":
        job.selected = {f.index for f in job.files}
        job.touch()
        await _refresh_picker(query, job)
        await query.answer("Выбраны все файлы")
        return

    if action == "cancel":
        await pipeline.request_cancel(user_id)
        if query.message:
            await query.message.edit_text("Отменено.")
        await query.answer()
        return

    if action == "go":
        if not job.selected:
            await query.answer("Выберите хотя бы один файл", show_alert=True)
            return
        selected_size = sum(f.size for f in job.files if f.index in job.selected)
        err = await ensure_limits(settings, selected_size, settings.downloads_dir)
        if err:
            await query.answer(err, show_alert=True)
            return
        if query.message:
            await query.message.edit_text(
                f"Старт загрузки: {len(job.selected)} файл(ов), "
                f"{format_size(selected_size)}."
            )
        await query.answer("Старт")
        await pipeline.start_download(user_id)
        return

    await query.answer()


async def _refresh_picker(query: CallbackQuery, job) -> None:
    if not query.message:
        return
    await query.message.edit_text(
        picker_text(job),
        reply_markup=files_keyboard(job),
    )
