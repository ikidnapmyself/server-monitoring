"""Cluster profile coherence checks.

Detects conflicts in cluster role configuration (agent vs hub vs standalone).
"""

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "cluster"


def _get_role() -> str:
    has_hub_url = bool(getattr(settings, "HUB_URL", ""))
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)

    if has_hub_url and cluster_enabled:
        return "conflict"
    if has_hub_url:
        return "agent"
    if cluster_enabled:
        return "hub"
    return "standalone"


def run() -> list[CheckResult]:
    results: list[CheckResult] = []

    role = _get_role()
    secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")
    instance_id = getattr(settings, "INSTANCE_ID", "")

    if role == "conflict":
        results.append(
            CheckResult(
                level="error",
                message="Cluster role conflict: both HUB_URL and CLUSTER_ENABLED=1 are set",
                hint="An instance cannot be both an agent and a hub. Unset one.",
                category=CATEGORY,
            )
        )
        return results

    if role == "agent":
        if not secret:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Set WEBHOOK_SECRET_CLUSTER for signed payloads to the hub.",
                    category=CATEGORY,
                )
            )
        if not instance_id:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: INSTANCE_ID is empty",
                    hint="Set INSTANCE_ID to identify this agent (defaults to hostname at runtime).",
                    category=CATEGORY,
                )
            )

    if role == "hub":
        if not secret:
            results.append(
                CheckResult(
                    level="error",
                    message="Hub mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Hub must have WEBHOOK_SECRET_CLUSTER to verify agent payloads.",
                    category=CATEGORY,
                )
            )

    if not results:
        if role == "standalone":
            results.append(
                CheckResult(level="ok", message="Standalone mode (no cluster)", category=CATEGORY)
            )
        else:
            results.append(
                CheckResult(level="ok", message=f"Cluster role: {role}", category=CATEGORY)
            )

    return results
