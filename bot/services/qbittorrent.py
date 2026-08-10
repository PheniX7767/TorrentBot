from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import qbittorrentapi

from bot.config import Settings
from bot.services.jobs import TorrentFileInfo


logger = logging.getLogger(__name__)


class QBittorrentService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = qbittorrentapi.Client(
            host=settings.qbittorrent_url,
            username=settings.qbittorrent_username,
            password=settings.qbittorrent_password,
        )

    def connect(self) -> None:
        self._client.auth_log_in()
        logger.info("Connected to qBittorrent at %s", self._settings.qbittorrent_url)

    @property
    def client(self) -> qbittorrentapi.Client:
        return self._client

    def _hashes(self) -> set[str]:
        return {str(t.hash) for t in self._client.torrents_info()}

    def _wait_new_hash(self, before: set[str], timeout: float = 90.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = self._hashes() - before
            if new:
                return next(iter(new))
            time.sleep(0.4)
        raise TimeoutError("Torrent did not appear in qBittorrent")

    @staticmethod
    def _extract_added_hash(result: Any) -> str | None:
        """Newer qbittorrent-api returns TorrentsAddedMetadata instead of 'Ok.'."""
        if result is None or result == "Ok.":
            return None
        ids = getattr(result, "added_torrent_ids", None)
        if ids:
            return str(next(iter(ids)))
        if isinstance(result, dict) and result.get("added_torrent_ids"):
            return str(next(iter(result["added_torrent_ids"])))
        return None

    def _ensure_add_ok(self, result: Any, kind: str) -> str | None:
        """Validate add result; return known hash if API provided one."""
        added_hash = self._extract_added_hash(result)
        if result == "Ok." or result is None or added_hash:
            failure = getattr(result, "failure_count", 0) if result is not None else 0
            if isinstance(result, dict):
                failure = int(result.get("failure_count", 0))
            if failure:
                raise RuntimeError(f"qBittorrent add {kind} failed: {result}")
            return added_hash
        # Older servers sometimes return empty string on success
        if result == "":
            return None
        raise RuntimeError(f"qBittorrent add {kind} failed: {result}")

    def add_magnet(self, magnet: str, save_path: str) -> str:
        before = self._hashes()
        result = self._client.torrents_add(
            urls=magnet,
            save_path=save_path,
            is_paused=True,
            use_auto_tmm=False,
        )
        known = self._ensure_add_ok(result, "magnet")
        return known or self._wait_new_hash(before)

    def add_torrent_file(self, torrent_bytes: bytes, filename: str, save_path: str) -> str:
        before = self._hashes()
        suffix = ".torrent" if not filename.endswith(".torrent") else ""
        with tempfile.NamedTemporaryFile(suffix=suffix or ".torrent", delete=False) as tmp:
            tmp.write(torrent_bytes)
            tmp_path = tmp.name
        try:
            result = self._client.torrents_add(
                torrent_files=tmp_path,
                save_path=save_path,
                is_paused=True,
                use_auto_tmm=False,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        known = self._ensure_add_ok(result, "torrent")
        return known or self._wait_new_hash(before)

    def wait_metadata(self, torrent_hash: str, timeout: float = 180.0) -> list[TorrentFileInfo]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self._client.torrents_info(torrent_hashes=torrent_hash)
            if not info:
                time.sleep(1)
                continue
            files = self._client.torrents_files(torrent_hash=torrent_hash)
            if files:
                return [
                    TorrentFileInfo(index=int(f.index), name=str(f.name), size=int(f.size))
                    for f in files
                ]
            time.sleep(1)
        raise TimeoutError("Timeout waiting for torrent metadata")

    def torrent_name(self, torrent_hash: str) -> str:
        info = self._client.torrents_info(torrent_hashes=torrent_hash)
        if not info:
            return torrent_hash
        return str(info[0].name)

    def set_file_priorities(self, torrent_hash: str, selected: set[int], total_files: int) -> None:
        for idx in range(total_files):
            prio = 1 if idx in selected else 0
            self._client.torrents_file_priority(
                torrent_hash=torrent_hash,
                file_ids=idx,
                priority=prio,
            )

    def resume(self, torrent_hash: str) -> None:
        self._client.torrents_resume(torrent_hashes=torrent_hash)

    def progress(self, torrent_hash: str) -> dict[str, Any]:
        info = self._client.torrents_info(torrent_hashes=torrent_hash)
        if not info:
            return {"missing": True}
        t = info[0]
        return {
            "name": t.name,
            "progress": float(t.progress),
            "dlspeed": int(t.dlspeed),
            "eta": int(t.eta),
            "state": str(t.state),
            "size": int(t.size),
            "save_path": str(t.save_path),
        }

    def is_complete(self, torrent_hash: str, selected: set[int]) -> bool:
        files = self._client.torrents_files(torrent_hash=torrent_hash)
        if not files:
            return False
        for f in files:
            if int(f.index) not in selected:
                continue
            if float(f.progress) < 0.999:
                return False
        return True

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> None:
        try:
            self._client.torrents_delete(
                torrent_hashes=torrent_hash,
                delete_files=delete_files,
            )
        except qbittorrentapi.APIError as exc:
            logger.warning("Failed to delete torrent %s: %s", torrent_hash, exc)

    def content_paths(self, torrent_hash: str, selected: set[int], save_path: str) -> list[Path]:
        files = self._client.torrents_files(torrent_hash=torrent_hash)
        root = Path(save_path)
        paths: list[Path] = []
        for f in files:
            if int(f.index) not in selected:
                continue
            paths.append(root / str(f.name))
        return paths
