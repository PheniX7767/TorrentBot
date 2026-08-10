from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.youtube import YoutubeInfo, format_duration


def youtube_quality_keyboard() -> InlineKeyboardMarkup:
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


def youtube_picker_text(info: YoutubeInfo) -> str:
    uploader = f"\nКанал: {_esc(info.uploader)}" if info.uploader else ""
    return (
        f"<b>{_esc(info.title)}</b>{uploader}\n"
        f"Длительность: {format_duration(info.duration)}\n\n"
        "Выберите качество загрузки:"
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
