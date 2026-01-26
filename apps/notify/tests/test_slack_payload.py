"""Tests for Slack notification driver template rendering.

These tests verify that the Slack driver correctly renders templates
and produces valid JSON payloads for the Slack Block Kit API.
"""

import json

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.slack import SlackNotifyDriver


def make_messy_recommendation():
    return """[{'check': '**Check Summary**'\n\n '- checks_run: 5'\n\n '- passed: 5'\n\n '- failed: 0'\n\n '- checker_output_ref: '`checker:3bc972da-0d9f-41f1-92ad-87383798a19b:6d9dff7a-a7a6-4037-b680-59ac2eb07e38:check`'\n\n '- duration_ms: 1091.8', 'environment': 'development', 'incident_id': 6, 'ingest': '**Ingest Summary**']"""


def test_slack_template_renders_json_serializable():
    """Test that Slack template renders valid JSON-serializable Block Kit payload."""
    msg = NotificationMessage(
        title="Test Alert",
        message="Fallback",
        severity="info",
        channel="#dev",
        tags={"source": "unit-test"},
        context={
            "recommendations": [
                {"title": "Check Summary", "description": make_messy_recommendation()}
            ],
            "incident_id": 6,
            "source": "unittest",
        },
    )
    driver = SlackNotifyDriver()
    rendered = driver._render_message_templates(
        msg, {"webhook_url": "https://hooks.slack.com/services/T000/B000/XXX"}
    )

    # Template should render text
    assert rendered.get("text") is not None
    rendered_text = rendered["text"]

    # Should be JSON-parseable (slack_text.j2 outputs Block Kit JSON)
    stripped = rendered_text.strip()
    assert stripped.startswith("{") or stripped.startswith("[")

    payload = json.loads(rendered_text)

    # Should be JSON serializable
    dumped = json.dumps(payload, ensure_ascii=False)
    assert dumped


def test_slack_template_recommendations_not_raw_json():
    """Test that recommendations are rendered properly, not as raw Python repr."""
    msg = NotificationMessage(
        title="Test Alert",
        message="Fallback",
        severity="info",
        context={
            "recommendations": [
                {"title": "Check Summary", "description": make_messy_recommendation()}
            ],
            "incident_id": 6,
            "source": "unittest",
        },
    )
    driver = SlackNotifyDriver()
    rendered = driver._render_message_templates(
        msg, {"webhook_url": "https://hooks.slack.com/services/T000/B000/XXX"}
    )

    rendered_text = rendered.get("text")
    assert rendered_text is not None

    # Parse the JSON payload
    payload = json.loads(rendered_text)

    # Find blocks (payload may have "blocks" key)
    blocks = payload.get("blocks", [])
    if not blocks and isinstance(payload, list):
        blocks = payload

    # Find recommendation section text
    rec_texts = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "section"
        and b.get("text")
        and "recommendation" in b["text"]["text"].lower()
    ]

    # If recommendations section found, verify format
    if rec_texts:
        rec_text = rec_texts[0]
        # It should not contain raw Python list/dict markers at top-level
        assert not rec_text.strip().startswith("[{")
        assert "check_summary" not in rec_text.lower()
