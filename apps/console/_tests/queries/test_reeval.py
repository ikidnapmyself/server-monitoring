import pytest
from django.urls import reverse

from apps.alerts._tests.fixtures import make_reevaluable_alert
from apps.alerts.describing import alert_name
from apps.alerts.models import Alert, Node
from apps.checkers.checkers import checker_label
from apps.console.queries.reeval import NOTHING_OPEN, build_node_reeval_preview

pytestmark = pytest.mark.django_db


@pytest.fixture
def alert():
    return make_reevaluable_alert()


def test_a_missing_node_is_none():
    assert build_node_reeval_preview(999) is None


def test_the_node_preview_names_each_alert(alert):
    preview = build_node_reeval_preview(alert.node_id)

    assert len(preview.rows) == 1
    assert preview.rows[0].headline.startswith(alert_name(alert))
    assert preview.can_apply
    assert preview.title == "Re-evaluate the open alerts on web-03"
    assert preview.back_url == reverse("console:node", args=[alert.node_id])
    assert preview.url == reverse("console:node-reeval", args=[alert.node_id])
    assert "Applying queues 1 pipeline run." in " ".join(preview.notes)


def test_a_checker_narrows_even_when_it_matches_nothing(alert):
    preview = build_node_reeval_preview(alert.node_id, "memory")

    assert preview.rows == ()
    assert preview.empty == f"No open {checker_label('memory')} alerts on web-03."
    assert preview.title == f"Re-evaluate the {checker_label('memory')} alerts on web-03"
    assert not preview.can_apply
    assert preview.url.endswith("?checker=memory")


@pytest.mark.parametrize("checker", ["disk_{x}", ""])
def test_a_checker_nothing_knows_gets_a_fixed_title(alert, checker):
    preview = build_node_reeval_preview(alert.node_id, checker)

    assert preview.title == "Re-evaluate one checker's alerts on web-03"
    assert preview.empty == "No open alerts for that checker on web-03."
    assert preview.rows == ()
    assert not preview.can_apply


def test_a_checker_on_an_open_alert_keeps_its_label(alert):
    Alert.objects.filter(pk=alert.pk).update(labels={**alert.labels, "checker": "gpu_fans"})

    preview = build_node_reeval_preview(alert.node_id, "gpu_fans")

    assert preview.title == f"Re-evaluate the {checker_label('gpu_fans')} alerts on web-03"
    assert len(preview.rows) == 1


def test_an_empty_node_wide_scope_says_nothing_needs_it(alert):
    Alert.objects.filter(pk=alert.pk).update(status="resolved")

    preview = build_node_reeval_preview(alert.node_id)

    assert preview.empty == NOTHING_OPEN == "No open alerts need re-evaluation."


def test_an_unnamed_node_reads_as_unnamed(alert):
    Node.objects.filter(pk=alert.node_id).update(hostname="")

    preview = build_node_reeval_preview(alert.node_id)

    assert preview.title == "Re-evaluate the open alerts on Unnamed node"


def test_the_digest_covers_the_policy(alert):
    before = build_node_reeval_preview(alert.node_id).digest
    Node.objects.filter(pk=alert.node_id).update(
        config={"cpu": {"warning_threshold": 70, "critical_threshold": 99}}
    )

    assert build_node_reeval_preview(alert.node_id).digest != before
