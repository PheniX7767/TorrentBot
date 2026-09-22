from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class UserCounters:
    download: int = 0
    upload: int = 0


class UsageStats:
    """Persistent per-user download/upload counters in a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._users: dict[int, UserCounters] = {}
        self._loaded = False

    async def add_download(self, user_id: int, nbytes: int) -> None:
        if nbytes <= 0:
            return
        async with self._lock:
            await self._ensure_loaded()
            counters = self._users.setdefault(user_id, UserCounters())
            counters.download += nbytes
            await self._save()

    async def add_upload(self, user_id: int, nbytes: int) -> None:
        if nbytes <= 0:
            return
        async with self._lock:
            await self._ensure_loaded()
            counters = self._users.setdefault(user_id, UserCounters())
            counters.upload += nbytes
            await self._save()

    async def snapshot(self) -> dict[int, UserCounters]:
        async with self._lock:
            await self._ensure_loaded()
            return {
                uid: UserCounters(download=c.download, upload=c.upload)
                for uid, c in self._users.items()
            }

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        await asyncio.to_thread(self._load_sync)
        self._loaded = True

    def _load_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._users = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read usage stats %s: %s", self._path, exc)
            self._users = {}
            return
        users_raw = raw.get("users", {}) if isinstance(raw, dict) else {}
        loaded: dict[int, UserCounters] = {}
        if isinstance(users_raw, dict):
            for key, value in users_raw.items():
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
                if not isinstance(value, dict):
                    continue
                download = int(value.get("download", 0) or 0)
                upload = int(value.get("upload", 0) or 0)
                loaded[uid] = UserCounters(
                    download=max(0, download),
                    upload=max(0, upload),
                )
        self._users = loaded

    async def _save(self) -> None:
        await asyncio.to_thread(self._save_sync)

    def _save_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": {
                str(uid): {"download": c.download, "upload": c.upload}
                for uid, c in sorted(self._users.items())
            }
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._path)
