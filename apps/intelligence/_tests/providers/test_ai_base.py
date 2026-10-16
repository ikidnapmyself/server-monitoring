import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.intelligence.providers.ai_base import BaseAIProvider

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def describe_incident():
    """Overrides the package's patch so the prompt goes through the real describer."""


class _Provider(BaseAIProvider):
    name = "test"

    def _call_api(self, prompt: str) -> str:  # pragma: no cover
        raise NotImplementedError


def test_prompt_leads_with_the_real_incident_headline():
    node = Node.objects.create(instance_id="i-1", hostname="web-01")
    incident = Incident.objects.create(
        severity="critical", subject="checker:cpu", instance="i-1", node=node
    )
    Alert.objects.create(
        incident=incident,
        fingerprint="check:i-1:cpu",
        source="cluster",
        name="CPU usage",
        severity="critical",
        status="firing",
        description="CPU usage at 94%",
        labels={"checker": "cpu", "instance_id": "i-1"},
        raw_payload={"status": "critical", "metrics": {"cpu_percent": 94.0}},
        started_at=timezone.now(),
    )

    prompt = _Provider()._build_prompt(incident)

    assert prompt.startswith("Incident: High CPU usage on web-01\nDetail: CPU at 94%")


class _ProviderError(Exception):
    status_code = 401


class _FailingProvider(BaseAIProvider):
    name = "failing"

    def _call_api(self, prompt: str) -> str:
        raise _ProviderError("401 Unauthorized")


def test_analyze_raises_the_provider_error():
    incident = Incident.objects.create(severity="critical", subject="checker:cpu")

    with pytest.raises(_ProviderError, match="401"):
        _FailingProvider().analyze(incident)
