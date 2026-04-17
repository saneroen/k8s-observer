# k8s-observer

Production-grade, event-driven Kubernetes incident evidence service.

`k8s-observer` keeps a hot namespace state from Kubernetes watch streams and serves compact troubleshooting summaries through a stable API contract.

---

## Why this component exists

Incident systems need fast, reliable Kubernetes context. Pulling raw cluster data on every request creates:

- latency spikes,
- token bloat for downstream AI,
- higher operating cost,
- noisy and inconsistent incident reasoning.

`k8s-observer` fixes that with a strict boundary:

- **read-only evidence plane**,
- **event-driven state updates**,
- **deterministic-first output**,
- optional **local small-LLM summarization**.

---

## Production architecture

### 1) Event-driven state core

- Watch streams are started for pods, PVCs, events, quotas, and nodes in [`start_k8s_watchers()`](src/event_store.py:145).
- On any relevant change, affected namespaces are marked dirty via [`mark_dirty()`](src/event_store.py:75).
- A worker recomputes normalized namespace summaries in [`_worker_loop()`](src/event_store.py:117).
- API serves hot in-memory state from [`EventDrivenSummaryStore.get()`](src/event_store.py:61).

This gives near-real-time updates without per-request full-cluster reads.

### 2) Deterministic evidence layer

Normalized summary generation is performed by [`get_namespace_summary()`](src/k8s_client.py:29), including:

- pod phases/restarts/waiting reasons,
- PVC states and requested storage,
- resource quota hard/used,
- warning event reason digest,
- node pressure signals.

Contract type is defined by [`NamespaceSummary`](src/models.py:32).

### 3) Optional local LLM summarization

- LLM summarization is optional and additive in [`LocalLlmSummarizer.summarize()`](src/summarizer.py:73).
- API path applies LLM summary in [`namespace_summary()`](src/main.py:67).
- Cached by deterministic-summary hash in [`TimedValueCache.make_key()`](src/cache.py:43) to avoid repeated inference on unchanged state.

### 4) Health and readiness

- Liveness/operational info in [`/healthz`](src/main.py:29).
- Readiness gating in [`/readyz`](src/main.py:40), including LLM dependency behavior.

---

## Request flow

1. Kubernetes event arrives (watch stream).
2. Namespace is marked dirty.
3. Worker recomputes deterministic summary.
4. API request reads latest in-memory summary.
5. Optional LLM summary is attached (or reused from LLM summary cache).

---

## Token and latency impact

### Token efficiency

Typical troubleshooting context reduction:

- raw `kubectl`/event dump: **10k–40k tokens/incident**
- observer contract + focused context: **1.5k–6k tokens/incident**

Expected reduction: **~50% to 85%** depending on namespace complexity.

### Latency behavior

- Event-driven hot state removes repeated full fetch overhead from request path.
- Small prompts reduce LLM inference time.
- Hash-based LLM summary cache avoids redundant summarization when state does not change.

Result: lower p95 latency and more stable response-time under incident bursts.

---

## Cost model (practical estimate)

Example workload:

- 100 incidents/day,
- 30 days/month,
- 3,000 input + 300 output tokens/incident after compaction.

Monthly tokens:

- input: 9,000,000
- output: 900,000

If cloud model pricing is $5/M input + $15/M output:

- input ≈ $45
- output ≈ $13.5
- total ≈ **$58.5/month**

Local small-model mode can reduce variable API spend and improve data-locality controls. Exact cost depends on your infra (existing node vs dedicated VM/GPU).

> Numbers are directional planning estimates.

---

## Configuration

### LLM options

- `LLM_SUMMARY_MODE=none|ollama|external`
- `LLM_FAIL_OPEN=true|false`
- `LOCAL_LLM_BASE_URL`
- `LOCAL_LLM_MODEL`
- `LOCAL_LLM_TIMEOUT_SECONDS`
- `LOCAL_LLM_HEALTH_PATH`

### Event-driven store options

- `EVENT_DRIVEN_ENABLED=true|false`
- `EVENT_WORKER_INTERVAL_SECONDS` (default `1`)
- `EVENT_RESYNC_INTERVAL_SECONDS` (default `300`)

### LLM summary cache options

- `LLM_SUMMARY_CACHE_ENABLED=true|false`
- `LLM_SUMMARY_CACHE_TTL_SECONDS` (default `60`)
- `LLM_SUMMARY_CACHE_MAX_ENTRIES` (default `500`)

Helm values are in [`values.yaml`](helm/k8s-observer/values.yaml).

---

## API

- `GET /healthz`
- `GET /readyz`
- `GET /v1/namespaces/{namespace}/summary`

Entry point: [`app`](src/main.py:11).

---

## Deployment

### Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

### Helm deploy

```bash
helm upgrade --install k8s-observer ./helm/k8s-observer -n k8s-observer --create-namespace
```

Core RBAC is defined in [`clusterrole.yaml`](helm/k8s-observer/templates/clusterrole.yaml).

---

## Reliability checks

- Python compile check:
  - `python3 -m compileall ./src`
- Helm chart lint:
  - `helm lint ./helm/k8s-observer`
- Golden behavior checks:
  - dataset: [`golden_incidents.json`](evals/golden_incidents.json)
  - runner: [`evaluate_golden.py`](evals/evaluate_golden.py:52)

---

## Security model

- Read-only cluster permissions.
- No remediation writes in this component.
- Optional LLM stage can be isolated to local/private inference endpoints.

---

## Scope boundary

This service is intentionally not an autonomous remediation engine.

It is the Kubernetes evidence foundation for incident pipelines.

---

## License

Repository license applies.
