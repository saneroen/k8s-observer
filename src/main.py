from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException

from .cache import SummaryCache
from .cache import TimedValueCache
from .event_store import EventDrivenSummaryStore
from .event_store import start_k8s_watchers
from .k8s_client import K8sSummaryService
from .summarizer import LocalLlmSummarizer

app = FastAPI(title="k8s-observer-poc", version="0.1.0")
svc = K8sSummaryService()
summarizer = LocalLlmSummarizer()
summary_cache = SummaryCache.from_env(fetch_fn=svc.get_namespace_summary)
llm_summary_cache = TimedValueCache.from_env()
event_store = EventDrivenSummaryStore.from_env(
    fetch_fn=svc.get_namespace_summary,
    list_namespaces_fn=svc.list_namespaces,
)


@app.on_event("startup")
def startup() -> None:
    event_store.start()
    start_k8s_watchers(event_store, svc.core)
    summary_cache.start()


@app.on_event("shutdown")
def shutdown() -> None:
    event_store.stop()
    summary_cache.stop()


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "source_mode": svc.source_mode,
        "llm": summarizer.status(),
        "event_store": event_store.status(),
        "cache": summary_cache.status(),
        "llm_summary_cache": llm_summary_cache.status(),
    }


@app.get("/readyz")
def readyz() -> dict[str, object]:
    llm_status = summarizer.status()
    if llm_status.get("enabled") and not llm_status.get("healthy") and not llm_status.get("fail_open"):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "source_mode": svc.source_mode,
                "llm": llm_status,
            },
        )

    return {
        "status": "ready",
        "source_mode": svc.source_mode,
        "llm": llm_status,
    }


@app.get("/v1/namespaces/{namespace}/summary")
def namespace_summary(namespace: str):
    try:
        summary = event_store.get(namespace)
        key = llm_summary_cache.make_key(namespace, summary)
        llm_summary = llm_summary_cache.get(key)
        if llm_summary is None:
            llm_summary = summarizer.summarize(summary)
            if llm_summary:
                llm_summary_cache.put(key, llm_summary)
        summary.llm_summary = llm_summary
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to summarize namespace {namespace}: {exc}") from exc
