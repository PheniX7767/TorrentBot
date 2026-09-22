from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.config import Settings
from bot.services.delivery import (
    clean_directory,
    cleanup_paths,
    ensure_limits,
    format_eta,
    format_size,
    format_speed,
    prepare_sendables,
    send_documents,
)
from bot.services.jobs import JobState, job_manager
from bot.services.qbittorrent import QBittorrentService
from bot.services.usage_stats import UsageStats
from bot.services.ytdlp import YtdlpCancelled, download_media


logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        qbit: QBittorrentService,
        usage_stats: UsageStats,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.qbit = qbit
        self.usage_stats = usage_stats
        self._download_tasks: dict[int, asyncio.Task] = {}
        self._cancel_events: dict[int, asyncio.Event] = {}
        self._gc_task: asyncio.Task | None = None

    def start_background(self) -> None:
        self._gc_task = asyncio.create_task(self._gc_loop(), name="stale-gc")

    def _cancel_event(self, user_id: int) -> asyncio.Event:
        event = self._cancel_events.get(user_id)
        if event is None:
            event = asyncio.Event()
            self._cancel_events[user_id] = event
        return event

    async def shutdown(self) -> None:
        for event in self._cancel_events.values():
            event.set()
        for task in list(self._download_tasks.values()):
            if not task.done():
                task.cancel()
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()

    async def start_download(self, user_id: int) -> None:
        existing = self._download_tasks.get(user_id)
        if existing and not existing.done():
            return
        event = self._cancel_event(user_id)
        event.clear()
        self._download_tasks[user_id] = asyncio.create_task(
            self._run_download(user_id),
            name=f"download-job-{user_id}",
        )

    async def start_ytdlp_download(self, user_id: int) -> None:
        existing = self._download_tasks.get(user_id)
        if existing and not existing.done():
            return
        event = self._cancel_event(user_id)
        event.clear()
        self._download_tasks[user_id] = asyncio.create_task(
            self._run_ytdlp(user_id),
            name=f"ytdlp-job-{user_id}",
        )

    async def request_cancel(self, user_id: int) -> None:
        job = job_manager.get(user_id)
        self._cancel_event(user_id).set()
        if job.torrent_hash:
            await asyncio.to_thread(
                self.qbit.delete_torrent,
                job.torrent_hash,
                True,
            )
        task = self._download_tasks.get(user_id)
        if task and not task.done():
            task.cancel()
        job_manager.cancel(user_id)
        if job.save_path:
            await clean_directory(Path(job.save_path))
        job_manager.reset(user_id)
        self._download_tasks.pop(user_id, None)

    async def _run_download(self, user_id: int) -> None:
        job = job_manager.get(user_id)
        assert job.torrent_hash and job.chat_id and job.save_path
        torrent_hash = job.torrent_hash
        chat_id = job.chat_id
        save_path = Path(job.save_path)
        cancel = self._cancel_event(user_id)

        try:
            job.state = JobState.DOWNLOADING
            job.touch()
            await asyncio.to_thread(
                self.qbit.set_file_priorities,
                torrent_hash,
                job.selected,
                len(job.files),
            )
            await asyncio.to_thread(self.qbit.resume, torrent_hash)

            progress_msg = await self.bot.send_message(
                chat_id,
                "Загрузка началась…",
            )
            job.progress_message_id = progress_msg.message_id

            while not cancel.is_set():
                if await asyncio.to_thread(
                    self.qbit.is_complete,
                    torrent_hash,
                    job.selected,
                ):
                    break
                prog = await asyncio.to_thread(self.qbit.progress, torrent_hash)
                if prog.get("missing"):
                    raise RuntimeError("Торрент исчез из qBittorrent")
                state = str(prog.get("state", "")).lower()
                if state in {"error", "missingfiles", "unknown"}:
                    raise RuntimeError(
                        "qBittorrent вернул состояние "
                        f"«{prog.get('state')}». Частая причина — нет прав на запись "
                        f"в {save_path}. На VPS выполните: "
                        f"chown -R {self.settings.puid}:{self.settings.pgid} "
                        f"{self.settings.downloads_dir} "
                        "(или DATA_DIR/downloads на хосте) и перезапустите задачу."
                    )
                text = (
                    f"<b>Загрузка</b>: {_esc(str(prog.get('name', '')))}\n"
                    f"Прогресс: {float(prog['progress']) * 100:.1f}%\n"
                    f"Скорость: {format_speed(int(prog['dlspeed']))}\n"
                    f"ETA: {format_eta(int(prog['eta']))}\n"
                    f"Состояние: {prog.get('state')}"
                )
                job.status_text = text
                job.touch()
                await self._safe_edit(chat_id, job.progress_message_id, text)
                await asyncio.sleep(self.settings.progress_edit_seconds)

            if cancel.is_set() or job.state == JobState.CANCELLED:
                return

            await self._safe_edit(
                chat_id,
                job.progress_message_id,
                "Скачивание завершено, готовлю отправку…",
            )

            paths = await asyncio.to_thread(
                self.qbit.content_paths,
                torrent_hash,
                job.selected,
                str(save_path),
            )
            await asyncio.to_thread(self.qbit.delete_torrent, torrent_hash, False)
            job.torrent_hash = None

            download_bytes = sum(p.stat().st_size for p in paths if p.exists())
            await self.usage_stats.add_download(user_id, download_bytes)

            job.state = JobState.DELIVERING
            job.touch()

            work_dir = save_path / "_send"
            sendables = await prepare_sendables(
                paths,
                work_dir,
                self.settings.zip_part_bytes,
            )
            if not sendables:
                raise RuntimeError("Не найдены скачанные файлы для отправки")

            await send_documents(
                self.bot,
                chat_id,
                sendables,
                caption_prefix=job.torrent_name[:100] if job.torrent_name else "",
                usage_stats=self.usage_stats,
                user_id=user_id,
            )

            await cleanup_paths(sendables)
            await clean_directory(save_path)
            job.state = JobState.DONE
            job.status_text = "Готово."
            job.touch()
            await self.bot.send_message(
                chat_id,
                "Готово. Файлы отправлены, временные данные удалены.",
            )
            job_manager.reset(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Job failed for user %s", user_id)
            job.state = JobState.ERROR
            job.error = str(exc)
            job.touch()
            try:
                if job.torrent_hash:
                    await asyncio.to_thread(self.qbit.delete_torrent, job.torrent_hash, True)
            except Exception:
                logger.exception("Cleanup torrent after error failed")
            if job.save_path:
                await clean_directory(Path(job.save_path))
            if job.chat_id:
                await self.bot.send_message(
                    job.chat_id,
                    f"Ошибка: {_esc(str(exc))}\nИспользуйте /cancel или /clean при необходимости.",
                )
            job_manager.reset(user_id)
        finally:
            self._download_tasks.pop(user_id, None)

    async def _run_ytdlp(self, user_id: int) -> None:
        job = job_manager.get(user_id)
        assert job.kind == "ytdlp" and job.chat_id and job.save_path and job.source_url
        chat_id = job.chat_id
        save_path = Path(job.save_path)
        url = job.source_url
        fmt = job.media_format or "best"
        cancel = self._cancel_event(user_id)

        try:
            job.state = JobState.DOWNLOADING
            job.touch()
            if job.progress_message_id is None:
                progress_msg = await self.bot.send_message(chat_id, "Скачиваю…")
                job.progress_message_id = progress_msg.message_id

            progress_state: dict[str, str] = {"text": "Скачиваю…"}

            def cancel_check() -> bool:
                return cancel.is_set()

            def progress_cb(d: dict) -> None:
                if d.get("status") == "downloading":
                    progress_state["text"] = (
                        f"{_esc(job.torrent_name or url)}\n"
                        f"Прогресс: {d.get('_percent_str', '—').strip()}\n"
                        f"Скорость: {d.get('_speed_str', '—').strip()}\n"
                        f"ETA: {d.get('_eta_str', '—').strip()}"
                    )
                elif d.get("status") == "finished":
                    progress_state["text"] = (
                        f"{_esc(job.torrent_name or url)}\n"
                        "Скачивание завершено, обрабатываю…"
                    )

            download_task = asyncio.create_task(
                asyncio.to_thread(
                    download_media,
                    url,
                    save_path,
                    fmt,
                    cancel_check,
                    progress_cb,
                    self.settings.ytdlp_cookies_file,
                )
            )

            while not download_task.done():
                if cancel.is_set():
                    break
                job.status_text = progress_state["text"]
                job.touch()
                await self._safe_edit(chat_id, job.progress_message_id, progress_state["text"])
                await asyncio.wait({download_task}, timeout=self.settings.progress_edit_seconds)

            if cancel.is_set() or job.state == JobState.CANCELLED:
                if not download_task.done():
                    download_task.cancel()
                return

            try:
                paths = await download_task
            except YtdlpCancelled:
                await self.bot.send_message(chat_id, "Загрузка отменена.")
                job_manager.reset(user_id)
                return

            total_size = sum(p.stat().st_size for p in paths if p.exists())
            err = await ensure_limits(self.settings, total_size, self.settings.downloads_dir)
            if err:
                raise RuntimeError(err)

            await self.usage_stats.add_download(user_id, total_size)

            await self._safe_edit(
                chat_id,
                job.progress_message_id,
                "Скачивание завершено, готовлю отправку…",
            )
            job.state = JobState.DELIVERING
            job.touch()

            work_dir = save_path / "_send"
            sendables = await prepare_sendables(
                paths,
                work_dir,
                self.settings.zip_part_bytes,
            )
            if not sendables:
                raise RuntimeError("Не найдены скачанные файлы для отправки")

            await send_documents(
                self.bot,
                chat_id,
                sendables,
                caption_prefix=job.torrent_name[:100] if job.torrent_name else "",
                usage_stats=self.usage_stats,
                user_id=user_id,
            )

            await cleanup_paths(sendables)
            await clean_directory(save_path)
            job.state = JobState.DONE
            job.status_text = "Готово."
            job.touch()
            await self.bot.send_message(
                chat_id,
                "Готово. Файлы отправлены, временные данные удалены.",
            )
            job_manager.reset(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("yt-dlp job failed for user %s", user_id)
            job.state = JobState.ERROR
            job.error = str(exc)
            job.touch()
            if job.save_path:
                await clean_directory(Path(job.save_path))
            if job.chat_id:
                await self.bot.send_message(
                    job.chat_id,
                    f"Ошибка: {_esc(str(exc))}\n"
                    "Используйте /cancel или /clean при необходимости.",
                )
            job_manager.reset(user_id)
        finally:
            self._download_tasks.pop(user_id, None)

    async def _safe_edit(self, chat_id: int, message_id: int | None, text: str) -> None:
        if message_id is None:
            return
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("edit_message_text: %s", exc)

    async def _gc_loop(self) -> None:
        import time

        while True:
            try:
                await asyncio.sleep(3600)
                max_age = self.settings.stale_job_hours * 3600
                for job in list(job_manager.active_jobs()):
                    if job.user_id is None:
                        continue
                    if (time.time() - job.created_at) > max_age:
                        logger.warning("Stale active job for user %s — cancelling", job.user_id)
                        await self.request_cancel(job.user_id)
                root = Path(self.settings.downloads_dir)
                if root.exists():
                    await clean_directory(root, older_than_seconds=float(max_age))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GC loop error")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ensure_writable_dir(path: Path, puid: int, pgid: int) -> Path:
    """Create dir and make it writable for qBittorrent (linuxserver PUID/PGID)."""
    import os
    import stat

    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(
            path,
            stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH,
        )
    except OSError as exc:
        logger.warning("chmod %s failed: %s", path, exc)
    try:
        os.chown(path, puid, pgid)
    except OSError as exc:
        logger.warning("chown %s to %s:%s failed: %s", path, puid, pgid, exc)
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except OSError:
            pass
    return path


def new_job_dir(downloads_dir: str, job_id: str, puid: int = 1000, pgid: int = 1000) -> Path:
    root = ensure_writable_dir(Path(downloads_dir), puid, pgid)
    return ensure_writable_dir(root / job_id, puid, pgid)


async def force_clean_downloads(settings: Settings, qbit: QBittorrentService) -> int:
    """Remove all torrents with files and wipe downloads dir contents."""
    try:
        torrents = await asyncio.to_thread(lambda: list(qbit.client.torrents_info()))
        for t in torrents:
            await asyncio.to_thread(qbit.delete_torrent, str(t.hash), True)
    except Exception:
        logger.exception("force_clean: qbit delete failed")
    root = Path(settings.downloads_dir)
    removed = 0
    if root.exists():
        for child in list(root.iterdir()):
            try:
                if child.is_file():
                    child.unlink(missing_ok=True)
                else:
                    shutil.rmtree(child, ignore_errors=True)
                removed += 1
            except OSError:
                pass
    job_manager.reset_all()
    return removed


__all__ = [
    "Pipeline",
    "new_job_dir",
    "ensure_writable_dir",
    "force_clean_downloads",
    "ensure_limits",
    "format_size",
]
