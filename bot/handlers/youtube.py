from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Settings
from bot.keyboards.youtube import youtube_picker_text, youtube_quality_keyboard
from bot.services.jobs import JobState, job_manager
from bot.services.pipeline import new_job_dir
from bot.services.youtube import YOUTUBE_RE, extract_youtube_url, fetch_info


router = Router(name="youtube")


@router.message(F.text.regexp(YOUTUBE_RE))
async def on_youtube_url(message: Message, settings: Settings) -> None:
    assert message.text and message.from_user
    url = extract_youtube_url(message.text)
    if not url:
        return

    user_id = message.from_user.id
    job = job_manager.try_acquire(user_id, message.chat.id)
    if job is None:
        await message.answer(
            "У вас уже есть активная задача. Дождитесь окончания или /cancel."
        )
        return

    job.kind = "youtube"
    job.youtube_url = url
    status = await message.answer("Получаю информацию о видео…")

    job_id = uuid.uuid4().hex[:12]
    save_path = new_job_dir(
        settings.downloads_dir,
        job_id,
        puid=settings.puid,
        pgid=settings.pgid,
    )
    job.save_path = str(save_path)

    try:
        info = await asyncio.to_thread(fetch_info, url)
        job.torrent_name = info.title
        job.youtube_url = info.webpage_url
        job.state = JobState.SELECTING
        job.touch()
        picker = await status.edit_text(
            youtube_picker_text(info),
            reply_markup=youtube_quality_keyboard(),
        )
        job.picker_message_id = picker.message_id
    except Exception as exc:
        job.state = JobState.ERROR
        job.error = str(exc)
        try:
            import shutil

            shutil.rmtree(Path(save_path), ignore_errors=True)
        except Exception:
            pass
        job_manager.reset(user_id)
        await status.edit_text(f"Не удалось разобрать YouTube-ссылку: {exc}")
