from __future__ import annotations

import os
import hashlib
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Callable

from .models import NamespaceSummary


@dataclass
class CacheEntry:
    value: NamespaceSummary
    fetched_at: float
    refreshing: bool = False


@dataclass
class TimedValueEntry:
    value: str
    stored_at: float


@dataclass
class TimedValueCache:
    enabled: bool = True
    ttl_seconds: float = 60.0
    max_entries: int = 500
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _entries: dict[str, TimedValueEntry] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> TimedValueCache:
        return cls(
            enabled=os.getenv("LLM_SUMMARY_CACHE_ENABLED", "true").lower() == "true",
            ttl_seconds=float(os.getenv("LLM_SUMMARY_CACHE_TTL_SECONDS", "60")),
            max_entries=int(os.getenv("LLM_SUMMARY_CACHE_MAX_ENTRIES", "500")),
        )

    def make_key(self, namespace: str, summary: NamespaceSummary) -> str:
        payload = summary.json().encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return f"{namespace}:{digest}"

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            if now - entry.stored_at > self.ttl_seconds:
                self._entries.pop(key, None)
                return None
            return entry.value

    def put(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._entries) >= self.max_entries and key not in self._entries:
                oldest_key = min(self._entries.items(), key=lambda kv: kv[1].stored_at)[0]
                self._entries.pop(oldest_key, None)
            self._entries[key] = TimedValueEntry(value=value, stored_at=time.time())

    def status(self) -> dict[str, object]:
        with self._lock:
            size = len(self._entries)
        return {
            "enabled": self.enabled,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "entries": size,
        }


@dataclass
class SummaryCache:
    fetch_fn: Callable[[str], NamespaceSummary]
    enabled: bool = True
    ttl_seconds: float = 30.0
    stale_seconds: float = 120.0
    max_entries: int = 200
    prefetch_enabled: bool = False
    prefetch_namespaces: list[str] = field(default_factory=list)
    prefetch_interval_seconds: float = 15.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _entries: dict[str, CacheEntry] = field(default_factory=dict)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _prefetch_thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, fetch_fn: Callable[[str], NamespaceSummary]) -> SummaryCache:
        prefetch_namespaces = [
            ns.strip()
            for ns in os.getenv("SUMMARY_PREFETCH_NAMESPACES", "").split(",")
            if ns.strip()
        ]
        return cls(
            fetch_fn=fetch_fn,
            enabled=os.getenv("SUMMARY_CACHE_ENABLED", "true").lower() == "true",
            ttl_seconds=float(os.getenv("SUMMARY_CACHE_TTL_SECONDS", "30")),
            stale_seconds=float(os.getenv("SUMMARY_CACHE_STALE_SECONDS", "120")),
            max_entries=int(os.getenv("SUMMARY_CACHE_MAX_ENTRIES", "200")),
            prefetch_enabled=os.getenv("SUMMARY_PREFETCH_ENABLED", "false").lower() == "true",
            prefetch_namespaces=prefetch_namespaces,
            prefetch_interval_seconds=float(os.getenv("SUMMARY_PREFETCH_INTERVAL_SECONDS", "15")),
        )

    def start(self) -> None:
        if not self.enabled or not self.prefetch_enabled or not self.prefetch_namespaces:
            return
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return

        self._prefetch_thread = threading.Thread(target=self._prefetch_loop, daemon=True)
        self._prefetch_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def get(self, namespace: str) -> NamespaceSummary:
        if not self.enabled:
            return self.fetch_fn(namespace)

        now = time.time()

        with self._lock:
            entry = self._entries.get(namespace)

        if entry is None:
            return self._refresh(namespace, sync=True)

        age = now - entry.fetched_at

        # Fresh cache hit.
        if age <= self.ttl_seconds:
            return entry.value

        # Stale-while-revalidate window.
        if age <= self.ttl_seconds + self.stale_seconds:
            self._refresh(namespace, sync=False)
            return entry.value

        # Too old; refresh synchronously.
        return self._refresh(namespace, sync=True)

    def status(self) -> dict[str, object]:
        with self._lock:
            size = len(self._entries)

        return {
            "enabled": self.enabled,
            "ttl_seconds": self.ttl_seconds,
            "stale_seconds": self.stale_seconds,
            "max_entries": self.max_entries,
            "entries": size,
            "prefetch_enabled": self.prefetch_enabled,
            "prefetch_namespaces": self.prefetch_namespaces,
            "prefetch_interval_seconds": self.prefetch_interval_seconds,
        }

    def _prefetch_loop(self) -> None:
        while not self._stop_event.is_set():
            for namespace in self.prefetch_namespaces:
                self._refresh(namespace, sync=False)
            self._stop_event.wait(self.prefetch_interval_seconds)

    def _refresh(self, namespace: str, sync: bool) -> NamespaceSummary:
        if sync:
            value = self.fetch_fn(namespace)
            self._store(namespace, value)
            return value

        with self._lock:
            entry = self._entries.get(namespace)
            if entry and entry.refreshing:
                return entry.value
            if entry:
                entry.refreshing = True

        def _bg_refresh() -> None:
            try:
                value = self.fetch_fn(namespace)
                self._store(namespace, value)
            except Exception:
                with self._lock:
                    existing = self._entries.get(namespace)
                    if existing:
                        existing.refreshing = False

        if not entry:
            # No cache yet: a sync fetch avoids empty-return edge cases.
            value = self.fetch_fn(namespace)
            self._store(namespace, value)
            return value

        thread = threading.Thread(target=_bg_refresh, daemon=True)
        thread.start()
        return entry.value

    def _store(self, namespace: str, value: NamespaceSummary) -> None:
        with self._lock:
            if len(self._entries) >= self.max_entries and namespace not in self._entries:
                oldest_key = min(self._entries.items(), key=lambda kv: kv[1].fetched_at)[0]
                self._entries.pop(oldest_key, None)

            self._entries[namespace] = CacheEntry(value=value, fetched_at=time.time(), refreshing=False)
