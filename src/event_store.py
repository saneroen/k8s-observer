from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from queue import Empty
from queue import Queue
from typing import Callable

from kubernetes import watch

from .models import NamespaceSummary


@dataclass
class EventDrivenSummaryStore:
    fetch_fn: Callable[[str], NamespaceSummary]
    list_namespaces_fn: Callable[[], list[str]]
    enabled: bool = True
    worker_interval_seconds: float = 1.0
    resync_interval_seconds: float = 300.0
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _summaries: dict[str, NamespaceSummary] = field(default_factory=dict)
    _updated_at: dict[str, float] = field(default_factory=dict)
    _dirty: set[str] = field(default_factory=set)
    _dirty_queue: Queue[str] = field(default_factory=Queue)
    _threads: list[threading.Thread] = field(default_factory=list)
    _watchers_ok: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        fetch_fn: Callable[[str], NamespaceSummary],
        list_namespaces_fn: Callable[[], list[str]],
    ) -> EventDrivenSummaryStore:
        return cls(
            fetch_fn=fetch_fn,
            list_namespaces_fn=list_namespaces_fn,
            enabled=os.getenv("EVENT_DRIVEN_ENABLED", "true").lower() == "true",
            worker_interval_seconds=float(os.getenv("EVENT_WORKER_INTERVAL_SECONDS", "1")),
            resync_interval_seconds=float(os.getenv("EVENT_RESYNC_INTERVAL_SECONDS", "300")),
        )

    def start(self) -> None:
        if not self.enabled:
            return

        # Prime queue so first reads are warm for known namespaces.
        try:
            for ns in self.list_namespaces_fn():
                self.mark_dirty(ns)
        except Exception:
            pass

        self._start_thread("worker", self._worker_loop)
        self._start_thread("resync", self._resync_loop)

    def stop(self) -> None:
        self._stop_event.set()

    def get(self, namespace: str) -> NamespaceSummary:
        if not self.enabled:
            return self.fetch_fn(namespace)

        with self._lock:
            summary = self._summaries.get(namespace)
        if summary is not None:
            return summary

        # Cold namespace: fetch once and store.
        summary = self.fetch_fn(namespace)
        self._store(namespace, summary)
        return summary

    def mark_dirty(self, namespace: str) -> None:
        if not namespace:
            return
        with self._lock:
            if namespace in self._dirty:
                return
            self._dirty.add(namespace)
        self._dirty_queue.put(namespace)

    def mark_all_cached_dirty(self) -> None:
        with self._lock:
            namespaces = list(self._summaries.keys())
        for ns in namespaces:
            self.mark_dirty(ns)

    def status(self) -> dict[str, object]:
        with self._lock:
            cached = len(self._summaries)
            dirty = len(self._dirty)
            watchers_ok = dict(self._watchers_ok)
        return {
            "enabled": self.enabled,
            "cached_namespaces": cached,
            "dirty_namespaces": dirty,
            "worker_interval_seconds": self.worker_interval_seconds,
            "resync_interval_seconds": self.resync_interval_seconds,
            "watchers": watchers_ok,
        }

    def _store(self, namespace: str, summary: NamespaceSummary) -> None:
        with self._lock:
            self._summaries[namespace] = summary
            self._updated_at[namespace] = time.time()
            self._dirty.discard(namespace)

    def _start_thread(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"event-store-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                namespace = self._dirty_queue.get(timeout=self.worker_interval_seconds)
            except Empty:
                continue

            if not namespace:
                continue

            try:
                summary = self.fetch_fn(namespace)
                self._store(namespace, summary)
            except Exception:
                # keep dirty marker for retry path
                with self._lock:
                    self._dirty.add(namespace)

    def _resync_loop(self) -> None:
        while not self._stop_event.wait(self.resync_interval_seconds):
            self.mark_all_cached_dirty()


def start_k8s_watchers(store: EventDrivenSummaryStore, core_v1_api) -> None:
    if not store.enabled:
        return

    def run_watch(watch_name: str, list_fn: Callable, namespace_resolver: Callable[[object], str | None], mark_all_on_event: bool = False) -> None:
        while not store._stop_event.is_set():
            w = watch.Watch()
            try:
                with store._lock:
                    store._watchers_ok[watch_name] = True
                stream = w.stream(list_fn, timeout_seconds=60)
                for event in stream:
                    if store._stop_event.is_set():
                        break

                    obj = event.get("object")
                    if mark_all_on_event:
                        store.mark_all_cached_dirty()
                        continue

                    namespace = namespace_resolver(obj)
                    if namespace:
                        store.mark_dirty(namespace)
            except Exception:
                with store._lock:
                    store._watchers_ok[watch_name] = False
                time.sleep(2)
            finally:
                w.stop()

    watchers = [
        ("pods", core_v1_api.list_pod_for_all_namespaces, lambda obj: _meta_namespace(obj), False),
        (
            "pvcs",
            core_v1_api.list_persistent_volume_claim_for_all_namespaces,
            lambda obj: _meta_namespace(obj),
            False,
        ),
        ("events", core_v1_api.list_event_for_all_namespaces, lambda obj: _meta_namespace(obj), False),
        ("quotas", core_v1_api.list_resource_quota_for_all_namespaces, lambda obj: _meta_namespace(obj), False),
        ("nodes", core_v1_api.list_node, lambda obj: None, True),
    ]

    for name, list_fn, ns_fn, mark_all in watchers:
        store._start_thread(
            f"watch-{name}",
            lambda n=name, lf=list_fn, nf=ns_fn, ma=mark_all: run_watch(n, lf, nf, ma),
        )


def _meta_namespace(obj: object) -> str | None:
    metadata = getattr(obj, "metadata", None)
    if not metadata:
        return None
    namespace = getattr(metadata, "namespace", None)
    if isinstance(namespace, str) and namespace:
        return namespace
    return None
