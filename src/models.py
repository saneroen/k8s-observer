from __future__ import annotations

from pydantic import BaseModel


class PodSignal(BaseModel):
    name: str
    phase: str
    restarts: int
    waiting_reasons: list[str]


class PvcSignal(BaseModel):
    name: str
    phase: str
    storage_class: str | None
    requested_storage: str | None


class QuotaSignal(BaseModel):
    name: str
    hard: dict[str, str]
    used: dict[str, str]


class EventDigest(BaseModel):
    reason: str
    count: int
    sample_message: str


class NamespaceSummary(BaseModel):
    namespace: str
    pod_signals: list[PodSignal]
    pvc_signals: list[PvcSignal]
    quota_signals: list[QuotaSignal]
    warning_events: list[EventDigest]
    node_pressure_signals: list[str]
    source_mode: str
    llm_summary: str | None = None
