from __future__ import annotations

import os

import requests

from .models import NamespaceSummary


class LocalLlmSummarizer:
    def __init__(self) -> None:
        self.base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
        self.model = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:0.5b")
        self.mode = self._resolve_mode()
        self.enabled = self.mode != "none"
        self.fail_open = os.getenv("LLM_FAIL_OPEN", "true").lower() == "true"
        self.timeout_seconds = float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "12"))
        self.health_path = os.getenv("LOCAL_LLM_HEALTH_PATH", self._default_health_path())

    def _resolve_mode(self) -> str:
        mode = os.getenv("LLM_SUMMARY_MODE", "none").strip().lower()
        if mode in {"none", "ollama", "external"}:
            return mode

        # Backward compatibility: old feature-flag behavior maps to ollama mode.
        if os.getenv("ENABLE_LOCAL_LLM_SUMMARY", "false").lower() == "true":
            return "ollama"
        return "none"

    def _default_health_path(self) -> str:
        if self.mode == "ollama":
            return "/api/tags"
        return "/healthz"

    def status(self) -> dict[str, str | bool | None]:
        if not self.enabled:
            return {
                "enabled": False,
                "mode": self.mode,
                "healthy": True,
                "model": None,
                "base_url": None,
                "fail_open": self.fail_open,
            }

        healthy = self.health_check()
        return {
            "enabled": True,
            "mode": self.mode,
            "healthy": healthy,
            "model": self.model,
            "base_url": self.base_url,
            "fail_open": self.fail_open,
        }

    def health_check(self) -> bool:
        if not self.enabled:
            return True

        try:
            resp = requests.get(
                f"{self.base_url}{self.health_path}",
                timeout=min(self.timeout_seconds, 5),
            )
            return 200 <= resp.status_code < 500
        except Exception:
            return False

    def summarize(self, summary: NamespaceSummary) -> str | None:
        if not self.enabled:
            return None

        try:
            prompt = self._build_prompt(summary)
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            }

            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            body = resp.json()
            return (body.get("response") or "").strip() or None
        except Exception:
            if self.fail_open:
                return None
            raise

    def _build_prompt(self, summary: NamespaceSummary) -> str:
        return (
            "You are a Kubernetes troubleshooting summarizer. "
            "Write max 8 bullet points with only actionable findings and likely causes. "
            "Do not invent data.\n\n"
            f"Namespace: {summary.namespace}\n"
            f"Pods: {len(summary.pod_signals)}\n"
            f"PVCs: {len(summary.pvc_signals)}\n"
            f"Quotas: {len(summary.quota_signals)}\n"
            f"Warning event reasons: {[e.reason for e in summary.warning_events[:8]]}\n"
            f"Node pressure signals: {summary.node_pressure_signals}\n"
        )
