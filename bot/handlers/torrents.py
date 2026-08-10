from __future__ import annotations

import asyncio
import re
import uuid

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Settings
from bot.keyboards.files import files_keyboard, picker_text
from bot.services.jobs import JobState, job_manager
from bot.services.pipeline import new_job_dir
from bot.services.qbittorrent import QBittorrentService


router = Router(name="torrents")

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]+", re.IGNORECASE)


@router.message(F.text.regexp(MAGNET_RE))
async def on_magnet(
    message: Message,
    settings: Settings,
    qbit: QBittorrentService,
) -> None:
    assert message.text and message.from_user
    magnet = MAGNET_RE.search(message.text)
    if not magnet:
        return
    await _start_torrent_job(
        message,
        settings,
        qbit,
        magnet=magnet.group(0),
    )


@router.message(F.document)
async def on_torrent_document(
    message: Message,
    settings: Settings,
    qbit: QBittorrentService,
) -> None:
    assert message.from_user
    doc = message.document
    if doc is None:
        return
    name = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    if not (name.endswith(".torrent") or "bittorrent" in mime or mime == "application/x-bittorrent"):
        await message.answer("Пришлите файл .torrent или magnet-ссылку.")
        return

    file = await message.bot.get_file(doc.file_id)
    from io import BytesIO

    buf = BytesIO()
    await message.bot.download(file, destination=buf)
    torrent_bytes = buf.getvalue()
    if not torrent_bytes:
        await message.answer("Не удалось скачать .torrent файл.")
        return

    await _start_torrent_job(
        message,
        settings,
        qbit,
        torrent_bytes=torrent_bytes,
        torrent_filename=doc.file_name or "file.torrent",
    )


async def _start_torrent_job(
    message: Message,
    settings: Settings,
    qbit: QBittorrentService,
    magnet: str | None = None,
    torrent_bytes: bytes | None = None,
    torrent_filename: str = "file.torrent",
) -> None:
    assert message.from_user
    user_id = message.from_user.id
    job = job_manager.try_acquire(user_id, message.chat.id)
    if job is None:
        await message.answer(
            "У вас уже есть активная задача. Дождитесь окончания или /cancel."
        )
        return

    status = await message.answer("Добавляю торрент, жду метаданные…")
    job_id = uuid.uuid4().hex[:12]
    save_path = new_job_dir(
        settings.downloads_dir,
        job_id,
        puid=settings.puid,
        pgid=settings.pgid,
    )
    job.save_path = str(save_path)

    try:
        if magnet:
            torrent_hash = await asyncio.to_thread(
                qbit.add_magnet,
                magnet,
                str(save_path),
            )
        else:
            assert torrent_bytes is not None
            torrent_hash = await asyncio.to_thread(
                qbit.add_torrent_file,
                torrent_bytes,
                torrent_filename,
                str(save_path),
            )

        job.torrent_hash = torrent_hash
        files = await asyncio.to_thread(qbit.wait_metadata, torrent_hash)
        job.files = files
        job.torrent_name = await asyncio.to_thread(qbit.torrent_name, torrent_hash)
        job.selected = set()
        job.page = 0
        job.state = JobState.SELECTING
        job.touch()

        if len(files) == 1:
            job.selected = {files[0].index}

        picker = await status.edit_text(
            picker_text(job),
            reply_markup=files_keyboard(job),
        )
        job.picker_message_id = picker.message_id
    except Exception as exc:
        job.state = JobState.ERROR
        job.error = str(exc)
        if job.torrent_hash:
            await asyncio.to_thread(qbit.delete_torrent, job.torrent_hash, True)
        try:
            import shutil

            shutil.rmtree(save_path, ignore_errors=True)
        except Exception:
            pass
        job_manager.reset(user_id)
        await status.edit_text(f"Не удалось добавить торрент: {exc}")
