from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes


logger = logging.getLogger(__name__)

SOCKET_TIMEOUT = 60
CAROUSEL_MAX_ENTRIES = 20

# Schemed URL, or schemeless host/path (youtu.be/xxx, instagram.com/p/xxx).
URL_RE = re.compile(
    r"(https?://[^\s<>\"']+)|"
    r"((?:www\.)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}/[^\s<>\"']*)",
    re.IGNORECASE,
)

FORMAT_MAP = {
    "best": "bv*+ba/b",
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "480": "bv*[height<=480]+ba/b[height<=480]/b",
    "audio": "ba/b",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES

SKIP_IE_NAMES = frozenset(
    {
        "generic",
        "generic:featured-video",
        "html5",
        "html5media",
        "commonprotocols",
        "lazy",
    }
)

# Single-post carousels (several files, not a channel/profile).
CAROUSEL_IE_NAMES = frozenset(
    {
        "instagram",
        "instagram:story",
        "instagram:ios",
        "tiktok",
        "twitter",
        "twitter:card",
    }
)
CAROUSEL_IE_KEYS = frozenset(
    {
        "instagram",
        "instagramstory",
        "instagramios",
        "tiktok",
        "twitter",
        "twittercard",
    }
)


class YtdlpCancelled(Exception):
    """Raised from yt-dlp progress hook when the user cancels."""


class UnsupportedUrlError(RuntimeError):
    """URL has no named yt-dlp extractor."""


@dataclass
class MediaInfo:
    id: str
    title: str
    duration: int | None
    uploader: str | None
    webpage_url: str
    ie_name: str
    has_video: bool
    is_carousel: bool


def extract_media_url(text: str) -> str | None:
    match = URL_RE.search(text.strip())
    if not match:
        return None
    raw = (match.group(0) or "").rstrip(".,;:!?)")
    return raw or None


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "https://" + url
    return url


def _is_skipped_ie(name: str) -> bool:
    n = name.lower()
    return n in SKIP_IE_NAMES or n.startswith("generic")


def _is_carousel_ie(ie_name: str | None, ie_key: str | None = None) -> bool:
    name = (ie_name or "").lower()
    key = (ie_key or "").lower()
    if name in CAROUSEL_IE_NAMES:
        return True
    if key in CAROUSEL_IE_KEYS:
        return True
    return False


def named_extractor_for(url: str) -> str | None:
    """Return IE_NAME of the first named extractor that matches, or None."""
    url = normalize_url(url)
    for ie in gen_extractor_classes():
        name = str(getattr(ie, "IE_NAME", "") or "")
        if not name or _is_skipped_ie(name):
            continue
        if getattr(ie, "working", True) is False:
            continue
        try:
            if ie.suitable(url):
                return name
        except Exception:
            continue
    return None


def cookies_file_if_exists(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_file():
        return str(candidate)
    return None


LOGIN_HINT = (
    "Нужна авторизация. Положите Netscape cookies.txt "
    "в каталог cookies на сервере (см. README)."
)
UNAVAILABLE_HINT = "Пост недоступен (приватный, удалён или ограничен)."
BLOCKED_HINT = (
    "Сайт отклонил запрос (лимит или блокировка). "
    "Попробуйте позже или добавьте cookies.txt."
)
FEED_HINT = "Это плейлист, канал или профиль. Пришлите ссылку на одно видео или пост."
LIVE_HINT = "Прямые трансляции не скачиваются, пришлите ссылку на запись."

FEED_IE_SUFFIXES = frozenset(
    {
        "user",
        "tab",
        "playlist",
        "channel",
        "tag",
        "search",
        "collection",
        "sound",
        "profile",
    }
)


def _is_feed_ie(ie_name: str | None) -> bool:
    """Channel / profile / playlist extractors — not a single post."""
    name = (ie_name or "").lower()
    if ":" not in name:
        return False
    suffix = name.rsplit(":", 1)[-1]
    return suffix in FEED_IE_SUFFIXES


def _is_live_ie(ie_name: str | None) -> bool:
    name = (ie_name or "").lower()
    return name.endswith(":live") or name.endswith(":broadcast")


def extractor_reject_reason(ie_name: str | None) -> str | None:
    if _is_live_ie(ie_name):
        return LIVE_HINT
    if _is_feed_ie(ie_name):
        return FEED_HINT
    return None


def map_ytdlp_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    low = text.lower()
    if any(
        k in low
        for k in (
            "login required",
            "please log in",
            "sign in to",
            "not logged",
            "use --cookies",
            "cookies are needed",
            "cookies for",
        )
    ):
        return LOGIN_HINT
    if any(
        k in low
        for k in (
            "private video",
            "this video is private",
            "account is private",
            "video unavailable",
            "has been removed",
            "has been deleted",
            "doesn't exist",
            "does not exist",
        )
    ):
        return UNAVAILABLE_HINT
    if "429" in low or "too many requests" in low or "rate-limit" in low or "rate limit" in low:
        return BLOCKED_HINT
    if "403" in low or "forbidden" in low:
        return BLOCKED_HINT
    if "unsupported url" in low or "no video formats" in low or "unable to extract" in low:
        return "Не удалось найти медиа по этой ссылке."
    if len(text) > 500:
        return text[:500] + "…"
    return text


def _common_opts(cookies_file: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 5,
        "fragment_retries": 5,
    }
    cookie_path = cookies_file_if_exists(cookies_file)
    if cookie_path:
        opts["cookiefile"] = cookie_path
    return opts


def _make_ydl(opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
    with_impersonate = {**opts, "impersonate": "chrome"}
    try:
        return yt_dlp.YoutubeDL(with_impersonate)
    except Exception as exc:
        logger.warning("yt-dlp impersonate=chrome unavailable: %s", exc)
        return yt_dlp.YoutubeDL(opts)


def _is_live(info: dict[str, Any]) -> bool:
    if info.get("is_live"):
        return True
    if info.get("live_status") in {"is_live", "is_upcoming"}:
        return True
    return False


def _format_is_video(fmt: dict[str, Any]) -> bool:
    ext = str(fmt.get("ext") or "").lower()
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "heic", "bmp"}:
        return False
    vcodec = fmt.get("vcodec")
    if vcodec and vcodec != "none":
        return True
    video_ext = fmt.get("video_ext")
    if video_ext and video_ext not in {"none", "jpg", "png", "webp", "gif"}:
        return True
    return False


def _entry_has_video(info: dict[str, Any]) -> bool:
    if _is_live(info):
        return True
    formats = info.get("formats") or []
    if any(_format_is_video(f) for f in formats if isinstance(f, dict)):
        return True
    ext = str(info.get("ext") or "").lower()
    if f".{ext}" in VIDEO_SUFFIXES:
        return True
    vcodec = info.get("vcodec")
    if vcodec and vcodec != "none" and f".{ext}" not in IMAGE_SUFFIXES:
        return True
    return bool(info.get("duration") and ext not in {"jpg", "jpeg", "png", "webp", "gif"})


def _raise_if_live(info: dict[str, Any]) -> None:
    if _is_live(info):
        raise RuntimeError(LIVE_HINT)
    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            if entry and _is_live(entry):
                raise RuntimeError(LIVE_HINT)


def fetch_info(url: str, cookies_file: str | None = None) -> MediaInfo:
    url = normalize_url(url)
    ie_name = named_extractor_for(url)
    if not ie_name:
        raise UnsupportedUrlError("Этот сайт не поддерживается.")
    if _is_live_ie(ie_name):
        raise RuntimeError(LIVE_HINT)
    if _is_feed_ie(ie_name):
        raise RuntimeError(FEED_HINT)

    is_carousel = _is_carousel_ie(ie_name)
    opts = _common_opts(cookies_file)
    opts["skip_download"] = True
    opts["noplaylist"] = not is_carousel
    if is_carousel:
        opts["playlistend"] = CAROUSEL_MAX_ENTRIES

    try:
        with _make_ydl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(map_ytdlp_error(exc)) from exc

    if not info:
        raise RuntimeError("Не удалось получить информацию о медиа")

    _raise_if_live(info)

    resolved_ie = str(info.get("extractor") or ie_name)
    resolved_key = str(info.get("extractor_key") or info.get("ie_key") or "")
    is_carousel = _is_carousel_ie(resolved_ie, resolved_key) or is_carousel

    if info.get("_type") == "playlist":
        if not is_carousel:
            raise RuntimeError(FEED_HINT)
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("В этом посте нет файлов для скачивания.")
        entries = entries[:CAROUSEL_MAX_ENTRIES]
        has_video = any(_entry_has_video(e) for e in entries)
        duration = None
        if len(entries) == 1:
            raw_dur = entries[0].get("duration")
            duration = int(raw_dur) if raw_dur is not None else None
        title = str(info.get("title") or entries[0].get("title") or "media")
        uploader = info.get("uploader") or entries[0].get("uploader")
        return MediaInfo(
            id=str(info.get("id") or entries[0].get("id") or ""),
            title=title,
            duration=duration,
            uploader=str(uploader) if uploader else None,
            webpage_url=str(info.get("webpage_url") or url),
            ie_name=resolved_ie,
            has_video=has_video,
            is_carousel=True,
        )

    return MediaInfo(
        id=str(info.get("id") or ""),
        title=str(info.get("title") or "media"),
        duration=int(info["duration"]) if info.get("duration") is not None else None,
        uploader=str(info["uploader"]) if info.get("uploader") else None,
        webpage_url=str(info.get("webpage_url") or url),
        ie_name=resolved_ie,
        has_video=_entry_has_video(info),
        is_carousel=False,
    )


def download_media(
    url: str,
    out_dir: Path,
    format_key: str,
    cancel_check: Callable[[], bool] | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    cookies_file: str | None = None,
) -> list[Path]:
    url = normalize_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = FORMAT_MAP.get(format_key, FORMAT_MAP["best"])
    ie_name = named_extractor_for(url)
    is_carousel = _is_carousel_ie(ie_name)
    if is_carousel:
        outtmpl = str(out_dir / "%(playlist_index)s %(title).100B [%(id)s].%(ext)s")
    else:
        outtmpl = str(out_dir / "%(title).120B [%(id)s].%(ext)s")

    def hook(d: dict[str, Any]) -> None:
        if cancel_check and cancel_check():
            raise YtdlpCancelled("Загрузка отменена")
        if progress_cb:
            progress_cb(d)

    opts: dict[str, Any] = {
        **_common_opts(cookies_file),
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": not is_carousel,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
    }
    if is_carousel:
        opts["playlistend"] = CAROUSEL_MAX_ENTRIES
    if format_key == "audio":
        opts["format"] = "ba/b"
        opts["ignoreerrors"] = True
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
    try:
        with _make_ydl(opts) as ydl:
            ydl.download([url])
    except YtdlpCancelled:
        raise
    except Exception as exc:
        raise RuntimeError(map_ytdlp_error(exc)) from exc

    after = list(out_dir.iterdir()) if out_dir.exists() else []
    created = [
        p
        for p in after
        if p.is_file()
        and p.resolve() not in before
        and not p.name.endswith(".part")
        and not p.name.endswith(".ytdl")
        and p.suffix.lower() != ".json"
    ]
    if not created:
        created = [
            p
            for p in after
            if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
        ]
    if format_key == "audio":
        created = [p for p in created if p.suffix.lower() in AUDIO_SUFFIXES]
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
