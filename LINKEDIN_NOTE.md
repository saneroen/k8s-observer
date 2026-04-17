# Launch Note: Kubernetes Observer POC

Shipping today: **Kubernetes Observer POC** — a production-safe, read-only Kubernetes incident evidence API that makes AI troubleshooting faster, cheaper, and more reliable.

### Why I built this

Most incident copilots either:
- send huge raw K8s payloads to LLMs (slow + expensive), or
- jump too early into write-capable automation (risk).

This project takes a different path:
- deterministic, compact K8s evidence first
- optional small local LLM summary second
- no remediation writes in this component

### What it does

- Normalizes namespace troubleshooting context (pods, PVCs, quotas, warning events, node pressure)
- Exposes API endpoints for health/readiness/summary
- Supports in-cluster and kubeconfig fallback modes
- Adds optional local small-model summarization (`none | ollama | external`)

### Why this matters

- Lower token usage by compressing noisy cluster data into a compact schema
- Lower latency by reducing prompt size
- Lower risk with strict read-only boundary
- Better reliability with deterministic-first design and fail-open controls

### Cost & latency direction

- With prompt compaction, token usage can drop significantly (often 50–85% depending on incident shape)
- This helps both cloud-LLM and local-LLM deployments
- Local small models can reduce variable API costs and improve response-time consistency for short summaries

### Project links

- README: `k8s-observer-poc/README.md`
- Helm chart: `k8s-observer-poc/helm/k8s-observer`
- Golden evals: `k8s-observer-poc/evals/evaluate_golden.py`

If you are building incident AI for Kubernetes and want a safer evidence layer before automation, this is ready to try.

#Kubernetes #SRE #AIOps #DevOps #PlatformEngineering #LLM #IncidentResponse
