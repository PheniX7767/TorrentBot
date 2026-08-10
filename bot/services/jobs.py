from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any


class JobState(str, Enum):
    IDLE = "idle"
    FETCHING_METADATA = "fetching_metadata"
    SELECTING = "selecting"
    DOWNLOADING = "downloading"
    DELIVERING = "delivering"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class TorrentFileInfo:
    index: int
    name: str
    size: int


@dataclass
class Job:
    state: JobState = JobState.IDLE
    kind: str = "torrent"  # torrent | youtube
    user_id: int | None = None
    chat_id: int | None = None
    progress_message_id: int | None = None
    picker_message_id: int | None = None
    torrent_hash: str | None = None
    torrent_name: str = ""
    youtube_url: str | None = None
    youtube_format: str | None = None
    files: list[TorrentFileInfo] = field(default_factory=list)
    selected: set[int] = field(default_factory=set)
    page: int = 0
    save_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)
    status_text: str = ""

    def touch(self) -> None:
        self.updated_at = time()

    def is_active(self) -> bool:
        return self.state in {
            JobState.FETCHING_METADATA,
            JobState.SELECTING,
            JobState.DOWNLOADING,
            JobState.DELIVERING,
        }

    def summary(self) -> str:
        if self.state == JobState.IDLE:
            return "Нет активной задачи."
        label = "YouTube" if self.kind == "youtube" else "Раздача"
        name = self.torrent_name or self.youtube_url or self.torrent_hash or "—"
        parts = [
            f"Статус: <b>{self.state.value}</b>",
            f"Тип: {self.kind}",
            f"{label}: {_html_escape(name)}",
        ]
        if self.status_text:
            parts.append(self.status_text)
        if self.error:
            parts.append(f"Ошибка: {_html_escape(self.error)}")
        return "\n".join(parts)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class JobManager:
    """Per-user job slots so allowlisted users do not block each other."""

    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}

    def get(self, user_id: int) -> Job:
        return self._jobs.get(user_id) or Job(user_id=user_id)

    def try_acquire(self, user_id: int, chat_id: int) -> Job | None:
        current = self._jobs.get(user_id)
        if current and current.is_active():
            return None
        job = Job(
            state=JobState.FETCHING_METADATA,
            kind="torrent",
            user_id=user_id,
            chat_id=chat_id,
        )
        self._jobs[user_id] = job
        return job

    def reset(self, user_id: int) -> None:
        self._jobs.pop(user_id, None)

    def cancel(self, user_id: int) -> Job:
        job = self.get(user_id)
        if job.is_active() or job.state in {JobState.ERROR, JobState.DONE}:
            job.state = JobState.CANCELLED
            job.touch()
            self._jobs[user_id] = job
        return job

    def reset_all(self) -> None:
        self._jobs.clear()

    def active_jobs(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.is_active()]

    def to_debug(self, user_id: int) -> dict[str, Any]:
        j = self.get(user_id)
        return {
            "state": j.state.value,
            "kind": j.kind,
            "hash": j.torrent_hash,
            "files": len(j.files),
            "selected": sorted(j.selected),
        }


job_manager = JobManager()
