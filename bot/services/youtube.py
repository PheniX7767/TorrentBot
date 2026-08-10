from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp


logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/[^\s]+",
    re.IGNORECASE,
)

FORMAT_MAP = {
    "best": "bv*+ba/b",
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "480": "bv*[height<=480]+ba/b[height<=480]/b",
    "audio": "ba/b",
}


class YoutubeCancelled(Exception):
    """Raised from yt-dlp progress hook when the user cancels."""


@dataclass
class YoutubeInfo:
    id: str
    title: str
    duration: int | None
    uploader: str | None
    webpage_url: str


def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_RE.search(text.strip())
    if not match:
        return None
    return match.group(0)


def fetch_info(url: str) -> YoutubeInfo:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Не удалось получить информацию о видео")
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        if not entries:
            raise RuntimeError("Плейлист пуст; пришлите ссылку на одно видео")
        info = entries[0]
    return YoutubeInfo(
        id=str(info.get("id") or ""),
        title=str(info.get("title") or "youtube"),
        duration=int(info["duration"]) if info.get("duration") is not None else None,
        uploader=str(info["uploader"]) if info.get("uploader") else None,
        webpage_url=str(info.get("webpage_url") or url),
    )


def download_youtube(
    url: str,
    out_dir: Path,
    format_key: str,
    cancel_check: Callable[[], bool] | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = FORMAT_MAP.get(format_key, FORMAT_MAP["best"])
    outtmpl = str(out_dir / "%(title).120B [%(id)s].%(ext)s")

    def hook(d: dict[str, Any]) -> None:
        if cancel_check and cancel_check():
            raise YoutubeCancelled("Загрузка YouTube отменена")
        if progress_cb:
            progress_cb(d)

    opts: dict[str, Any] = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
    }
    if format_key == "audio":
        opts["format"] = "ba/b"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        opts["merge_output_format"] = "mp4"

    before = {p.resolve() for p in out_dir.iterdir()} if out_dir.exists() else set()
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    after = list(out_dir.iterdir()) if out_dir.exists() else []
    created = [
        p
        for p in after
        if p.is_file() and p.resolve() not in before and not p.name.endswith(".part")
    ]
    if not created:
        # fallback: any media file in dir
        created = [
            p
            for p in after
            if p.is_file()
            and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus"}
        ]
    if not created:
        raise RuntimeError("yt-dlp не создал выходной файл")
    created.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return created


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
