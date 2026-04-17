from __future__ import annotations

import json
from pathlib import Path


def derive_signals(summary: dict) -> set[str]:
    signals: set[str] = set()

    pod_signals = summary.get("pod_signals", [])
    pvc_signals = summary.get("pvc_signals", [])
    quota_signals = summary.get("quota_signals", [])
    warning_events = summary.get("warning_events", [])
    node_pressure = summary.get("node_pressure_signals", [])

    if any((p.get("restarts") or 0) > 3 for p in pod_signals):
        signals.add("pods_restarting")
    if any("CrashLoopBackOff" in (p.get("waiting_reasons") or []) for p in pod_signals):
        signals.add("crashloop_pattern")
    if any((pvc.get("phase") or "").lower() == "pending" for pvc in pvc_signals):
        signals.add("pvc_pending")

    for q in quota_signals:
        used = q.get("used", {}) or {}
        hard = q.get("hard", {}) or {}
        used_storage = _to_gi(used.get("requests.storage"))
        hard_storage = _to_gi(hard.get("requests.storage"))
        if hard_storage > 0 and used_storage / hard_storage >= 0.9:
            signals.add("storage_quota_high")

    if warning_events:
        signals.add("warning_events_present")
    if node_pressure:
        signals.add("node_pressure_present")

    return signals


def _to_gi(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip().lower()
    if value.endswith("gi"):
        return float(value[:-2])
    if value.endswith("mi"):
        return float(value[:-2]) / 1024
    if value.endswith("ti"):
        return float(value[:-2]) * 1024
    return 0.0


def main() -> None:
    path = Path(__file__).with_name("golden_incidents.json")
    cases = json.loads(path.read_text())

    total = len(cases)
    passed = 0

    for case in cases:
        name = case["name"]
        expected = set(case.get("expected_signals", []))
        actual = derive_signals(case["summary"])

        missing = sorted(expected - actual)
        if missing:
            print(f"FAIL {name}: missing={missing} actual={sorted(actual)}")
            continue

        print(f"PASS {name}: actual={sorted(actual)}")
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
