from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
import aiohttp

from bot.config import Settings, get_settings
from bot.services.usage_stats import UsageStats


logger = logging.getLogger(__name__)

# Local Bot API hard limit is 2000 MiB; keep a margin for headers/zip overhead.
TELEGRAM_SAFE_MAX_BYTES = 1900 * 1024 * 1024


def free_bytes(path: str | Path) -> int:
    usage = shutil.disk_usage(path)
    return int(usage.free)


def format_size(num: int) -> str:
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} Б"


def format_speed(bps: int) -> str:
    return f"{format_size(bps)}/с"


def format_eta(seconds: int) -> str:
    if seconds < 0 or seconds > 8640000:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {s}с"
    return f"{s}с"


async def ensure_limits(
    settings: Settings,
    selected_size: int,
    downloads_dir: str | Path,
) -> str | None:
    if selected_size > settings.max_selected_bytes:
        return (
            f"Выбрано слишком много: {format_size(selected_size)} "
            f"(лимит {format_size(settings.max_selected_bytes)})."
        )
    free = await asyncio.to_thread(free_bytes, downloads_dir)
    if free < settings.min_free_bytes:
        return (
            f"Мало места на диске: свободно {format_size(free)} "
            f"(нужно минимум {format_size(settings.min_free_bytes)})."
        )
    if free < selected_size + settings.min_free_bytes:
        return (
            f"Не хватит места: нужно ~{format_size(selected_size)}, "
            f"свободно {format_size(free)}."
        )
    return None


def _zip_stored(src: Path, dest_zip: Path) -> None:
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.write(src, arcname=src.name)


def _split_bytes(src: Path, part_size: int, dest_dir: Path, prefix: str) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with src.open("rb") as fh:
        index = 1
        while True:
            chunk = fh.read(part_size)
            if not chunk:
                break
            part_path = dest_dir / f"{prefix}.part{index:03d}"
            part_path.write_bytes(chunk)
            parts.append(part_path)
            index += 1
    return parts


def _try_zip_split(src: Path, dest_dir: Path, part_bytes: int) -> list[Path] | None:
    if shutil.which("zip") is None:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    part_m = max(1, part_bytes // (1024 * 1024))
    out_zip = dest_dir / f"{src.stem}.zip"
    try:
        subprocess.run(
            ["zip", "-0", "-j", "-s", f"{part_m}m", str(out_zip.name), str(src.resolve())],
            check=True,
            cwd=str(dest_dir),
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("zip -s failed, falling back to raw split: %s", exc)
        return None

    produced = sorted(dest_dir.glob(f"{src.stem}.z*")) + sorted(
        dest_dir.glob(f"{src.stem}.zip*")
    )
    seen: set[Path] = set()
    parts: list[Path] = []
    for p in produced:
        rp = p.resolve()
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        parts.append(p)
    return parts or None


async def prepare_sendables(
    paths: list[Path],
    work_dir: Path,
    zip_part_bytes: int,
) -> list[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    sendables: list[Path] = []
    part_limit = min(zip_part_bytes, TELEGRAM_SAFE_MAX_BYTES)

    for path in paths:
        if not path.exists():
            logger.warning("Missing file after download: %s", path)
            continue
        size = path.stat().st_size
        if size <= part_limit:
            sendables.append(path)
            continue

        file_work = work_dir / path.stem
        file_work.mkdir(parents=True, exist_ok=True)

        split_parts = await asyncio.to_thread(_try_zip_split, path, file_work, part_limit)
        if split_parts:
            sendables.extend(split_parts)
            continue

        zip_path = file_work / f"{path.stem}.zip"
        await asyncio.to_thread(_zip_stored, path, zip_path)
        if zip_path.stat().st_size <= part_limit:
            sendables.append(zip_path)
            continue

        parts = await asyncio.to_thread(
            _split_bytes,
            zip_path,
            part_limit,
            file_work,
            path.stem,
        )
        zip_path.unlink(missing_ok=True)
        sendables.extend(parts)

    return sendables


async def send_documents(
    bot: Bot,
    chat_id: int,
    paths: list[Path],
    caption_prefix: str = "",
    *,
    usage_stats: UsageStats | None = None,
    user_id: int | None = None,
) -> None:
    """Send files via local Bot API.

    Prefer ``file:///...`` (Bot API reads disk directly). Falls back to
    multipart upload through the local server (up to ~2 GB with --local).
    """
    settings = get_settings()
    api_url = f"{settings.bot_api_base_url.rstrip('/')}/bot{bot.token}/sendDocument"
    total = len(paths)
    timeout = aiohttp.ClientTimeout(total=3600, sock_connect=60, sock_read=3600)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i, path in enumerate(paths, start=1):
            resolved = path.resolve()
            if not resolved.is_file():
                raise FileNotFoundError(f"Не найден файл для отправки: {resolved}")
            size = resolved.stat().st_size
            if size > TELEGRAM_SAFE_MAX_BYTES:
                raise RuntimeError(
                    f"Файл слишком большой для Telegram ({format_size(size)}): {resolved.name}. "
                    "Нужна нарезка на части."
                )

            caption = None
            if total > 1:
                base = caption_prefix or resolved.name
                caption = f"{base} ({i}/{total})"
                if ".part" in resolved.name or resolved.suffix.startswith(".z"):
                    caption += "\nСоберите части в один архив перед распаковкой."
            elif caption_prefix:
                caption = caption_prefix

            file_uri = f"file://{resolved}"
            logger.info("Sending %s (%s) via %s", resolved.name, format_size(size), file_uri)

            payload: dict = {"chat_id": chat_id, "document": file_uri}
            if caption:
                payload["caption"] = caption

            async with session.post(api_url, json=payload) as resp:
                data = await resp.json(content_type=None)

            if data.get("ok"):
                if usage_stats is not None and user_id is not None:
                    await usage_stats.add_upload(user_id, size)
                continue

            description = str(data.get("description", data))
            logger.warning(
                "file:// send failed (%s), falling back to multipart upload",
                description,
            )

            # Fallback: stream upload to local Bot API (requires --local for >50MB)
            try:
                await bot.send_document(
                    chat_id=chat_id,
                    document=FSInputFile(resolved, filename=resolved.name),
                    caption=caption or None,
                    request_timeout=3600,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось отправить {resolved.name} ({format_size(size)}). "
                    f"file://: {description}; upload: {exc}. "
                    "Проверьте, что telegram-bot-api запущен с --local "
                    "(TELEGRAM_LOCAL=True) и volume /downloads смонтирован в оба контейнера."
                ) from exc

            if usage_stats is not None and user_id is not None:
                await usage_stats.add_upload(user_id, size)

async def cleanup_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError as exc:
            logger.warning("Cleanup failed for %s: %s", path, exc)


async def clean_directory(root: Path, older_than_seconds: float | None = None) -> int:
    import time

    if not root.exists():
        return 0
    removed = 0
    now = time.time()
    for child in list(root.iterdir()):
        try:
            if older_than_seconds is not None:
                mtime = child.stat().st_mtime
                if now - mtime < older_than_seconds:
                    continue
            if child.is_file():
                child.unlink(missing_ok=True)
            else:
                shutil.rmtree(child, ignore_errors=True)
            removed += 1
        except OSError as exc:
            logger.warning("GC failed for %s: %s", child, exc)
    return removed
