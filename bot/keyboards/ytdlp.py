from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.ytdlp import MediaInfo, format_duration


def quality_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Лучшее", callback_data="yt:go:best"),
        InlineKeyboardButton(text="1080p", callback_data="yt:go:1080"),
    )
    builder.row(
        InlineKeyboardButton(text="720p", callback_data="yt:go:720"),
        InlineKeyboardButton(text="480p", callback_data="yt:go:480"),
    )
    builder.row(
        InlineKeyboardButton(text="Только аудио (mp3)", callback_data="yt:go:audio"),
    )
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="yt:cancel"))
    return builder.as_markup()


def picker_text(info: MediaInfo) -> str:
    uploader = f"\nАвтор: {_esc(info.uploader)}" if info.uploader else ""
    duration_line = ""
    if info.has_video:
        duration_line = f"\nДлительность: {format_duration(info.duration)}"
    return (
        f"<b>{_esc(info.title)}</b>{uploader}{duration_line}\n\n"
        "Выберите качество загрузки:"
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
