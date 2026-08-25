from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Settings
from bot.keyboards.ytdlp import picker_text, quality_keyboard
from bot.services.jobs import JobState, job_manager
from bot.services.pipeline import Pipeline, new_job_dir
from bot.services.ytdlp import (
    URL_RE,
    extract_media_url,
    extractor_reject_reason,
    fetch_info,
    named_extractor_for,
    normalize_url,
)


router = Router(name="ytdlp")


@router.message(F.text.regexp(URL_RE))
async def on_media_url(
    message: Message,
    settings: Settings,
    pipeline: Pipeline,
) -> None:
    assert message.text and message.from_user
    raw = extract_media_url(message.text)
    if not raw:
        return

    url = normalize_url(raw)
    ie_name = named_extractor_for(url)
    if ie_name is None:
        await message.answer("Этот сайт не поддерживается.")
        return
    reject = extractor_reject_reason(ie_name)
    if reject:
        await message.answer(reject)
        return

    user_id = message.from_user.id
    job = job_manager.try_acquire(user_id, message.chat.id)
    if job is None:
        await message.answer(
            "У вас уже есть активная задача. Дождитесь окончания или /cancel."
        )
        return

    job.kind = "ytdlp"
    job.source_url = url
    status = await message.answer("Получаю информацию о медиа…")

    job_id = uuid.uuid4().hex[:12]
    save_path = new_job_dir(
        settings.downloads_dir,
        job_id,
        puid=settings.puid,
        pgid=settings.pgid,
    )
    job.save_path = str(save_path)

    try:
        info = await asyncio.to_thread(
            fetch_info,
            url,
            settings.ytdlp_cookies_file,
        )
        job.torrent_name = info.title
        job.source_url = info.webpage_url
        job.touch()

        if not info.has_video:
            job.media_format = "best"
            edited = await status.edit_text(
                f"<b>{_esc(info.title)}</b>\nСкачиваю…"
            )
            job.progress_message_id = edited.message_id
            await pipeline.start_ytdlp_download(user_id)
            return

        job.state = JobState.SELECTING
        picker = await status.edit_text(
            picker_text(info),
            reply_markup=quality_keyboard(),
        )
        job.picker_message_id = picker.message_id
    except Exception as exc:
        job.state = JobState.ERROR
        job.error = str(exc)
        try:
            shutil.rmtree(Path(save_path), ignore_errors=True)
        except Exception:
            pass
        job_manager.reset(user_id)
        await status.edit_text(f"Не удалось разобрать ссылку: {exc}")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
