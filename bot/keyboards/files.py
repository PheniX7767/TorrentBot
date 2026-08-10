from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.delivery import format_size
from bot.services.jobs import Job

FILES_PER_PAGE = 8


def files_keyboard(job: Job) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    files = job.files
    page = job.page
    start = page * FILES_PER_PAGE
    end = start + FILES_PER_PAGE
    page_files = files[start:end]

    for f in page_files:
        mark = "✓ " if f.index in job.selected else ""
        label = f"{mark}{f.name}"
        if len(label) > 60:
            label = label[:57] + "..."
        label = f"{label} ({format_size(f.size)})"
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"f:tog:{f.index}",
            )
        )

    nav: list[InlineKeyboardButton] = []
    total_pages = max(1, (len(files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"f:page:{page - 1}"))
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="f:noop",
        )
    )
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"f:page:{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(
        InlineKeyboardButton(text="Все", callback_data="f:all"),
        InlineKeyboardButton(text="Скачать выбранное", callback_data="f:go"),
    )
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="f:cancel"))
    return builder.as_markup()


def picker_text(job: Job) -> str:
    selected_size = sum(f.size for f in job.files if f.index in job.selected)
    return (
        f"<b>{_esc(job.torrent_name or 'Раздача')}</b>\n"
        f"Файлов: {len(job.files)}\n"
        f"Выбрано: {len(job.selected)} ({format_size(selected_size)})\n\n"
        "Отметьте нужные файлы и нажмите «Скачать выбранное»."
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
