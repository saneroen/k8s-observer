from __future__ import annotations

from collections import Counter

from kubernetes import client
from kubernetes import config
from kubernetes.client import ApiException

from .models import EventDigest
from .models import NamespaceSummary
from .models import PodSignal
from .models import PvcSignal
from .models import QuotaSignal


class K8sSummaryService:
    def __init__(self) -> None:
        self.source_mode = self._configure_client()
        self.core = client.CoreV1Api()

    def _configure_client(self) -> str:
        try:
            config.load_incluster_config()
            return "observer"
        except Exception:
            config.load_kube_config()
            return "pull"

    def get_namespace_summary(self, namespace: str) -> NamespaceSummary:
        pod_signals = self._pod_signals(namespace)
        pvc_signals = self._pvc_signals(namespace)
        quota_signals = self._quota_signals(namespace)
        warning_events = self._warning_events(namespace)
        node_pressure = self._node_pressure_signals(namespace)

        return NamespaceSummary(
            namespace=namespace,
            pod_signals=pod_signals,
            pvc_signals=pvc_signals,
            quota_signals=quota_signals,
            warning_events=warning_events,
            node_pressure_signals=node_pressure,
            source_mode=self.source_mode,
        )

    def list_namespaces(self) -> list[str]:
        namespaces = self.core.list_namespace().items
        return sorted({ns.metadata.name for ns in namespaces if ns.metadata and ns.metadata.name})

    def _pod_signals(self, namespace: str) -> list[PodSignal]:
        pods = self.core.list_namespaced_pod(namespace=namespace).items
        out: list[PodSignal] = []
        for pod in pods:
            restarts = 0
            waiting_reasons: list[str] = []
            for cs in pod.status.container_statuses or []:
                restarts += cs.restart_count or 0
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason:
                    waiting_reasons.append(waiting.reason)
            out.append(
                PodSignal(
                    name=pod.metadata.name,
                    phase=pod.status.phase or "Unknown",
                    restarts=restarts,
                    waiting_reasons=sorted(set(waiting_reasons)),
                ),
            )
        return out

    def _pvc_signals(self, namespace: str) -> list[PvcSignal]:
        pvcs = self.core.list_namespaced_persistent_volume_claim(namespace=namespace).items
        out: list[PvcSignal] = []
        for pvc in pvcs:
            requests = pvc.spec.resources.requests if pvc.spec and pvc.spec.resources else {}
            out.append(
                PvcSignal(
                    name=pvc.metadata.name,
                    phase=pvc.status.phase if pvc.status else "Unknown",
                    storage_class=pvc.spec.storage_class_name if pvc.spec else None,
                    requested_storage=requests.get("storage") if requests else None,
                ),
            )
        return out

    def _quota_signals(self, namespace: str) -> list[QuotaSignal]:
        quotas = self.core.list_namespaced_resource_quota(namespace=namespace).items
        out: list[QuotaSignal] = []
        for q in quotas:
            out.append(
                QuotaSignal(
                    name=q.metadata.name,
                    hard=dict(q.status.hard or {}),
                    used=dict(q.status.used or {}),
                ),
            )
        return out

    def _warning_events(self, namespace: str) -> list[EventDigest]:
        events = self.core.list_namespaced_event(namespace=namespace).items
        warnings = [e for e in events if (e.type or "").lower() == "warning"]
        reason_counts = Counter((e.reason or "Unknown") for e in warnings)
        sample_by_reason: dict[str, str] = {}
        for e in warnings:
            reason = e.reason or "Unknown"
            if reason not in sample_by_reason:
                sample_by_reason[reason] = e.message or ""

        return [
            EventDigest(reason=r, count=c, sample_message=sample_by_reason.get(r, ""))
            for r, c in reason_counts.most_common()
        ]

    def _node_pressure_signals(self, namespace: str) -> list[str]:
        pods = self.core.list_namespaced_pod(namespace=namespace).items
        node_names = sorted({p.spec.node_name for p in pods if p.spec and p.spec.node_name})
        if not node_names:
            return []

        out: list[str] = []
        for node_name in node_names:
            try:
                node = self.core.read_node(name=node_name)
            except ApiException:
                continue
            for cond in node.status.conditions or []:
                if cond.type in {"DiskPressure", "MemoryPressure", "PIDPressure"} and cond.status == "True":
                    out.append(f"{node_name}:{cond.type}")
        return sorted(set(out))
