import pytest
from django.urls import reverse

HTMX = {"HTTP_HX_REQUEST": "true"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


def test_the_page_lives_under_nodes():
    assert reverse("console:node", args=[7]) == "/console/nodes/7/"


@pytest.mark.django_db
@BOTH
def test_a_missing_node_is_the_console_404_page(client, node_viewer, headers):
    resp = client.get(reverse("console:node", args=[999]), **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This node doesn't exist." in body
    assert f'<a id="not-found-back" href="{reverse("console:nodes")}">Back to nodes</a>' in body
