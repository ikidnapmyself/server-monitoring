import json

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.slack import SlackNotifyDriver


def make_messy_recommendation():
    return """[{'check': '**Check Summary**'\n\n '- checks_run: 5'\n\n '- passed: 5'\n\n '- failed: 0'\n\n '- checker_output_ref: '`checker:3bc972da-0d9f-41f1-92ad-87383798a19b:6d9dff7a-a7a6-4037-b680-59ac2eb07e38:check`'\n\n '- duration_ms: 1091.8', 'environment': 'development', 'incident_id': 6, 'ingest': '**Ingest Summary**']"""


def test_compose_blocks_json_serializable():
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
    incident = driver._compose_incident_details(msg, {})
    blocks = driver._compose_blocks(msg, rendered.get("text"), incident)

    payload = {"blocks": blocks, "channel": "#dev"}
    # Should be JSON serializable
    dumped = json.dumps(payload, ensure_ascii=False)
    assert dumped


def test_recommendations_compact_and_not_raw_json():
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
    incident = driver._compose_incident_details(msg, {})
    blocks = driver._compose_blocks(msg, rendered.get("text"), incident)

    # Find recommendation section text
    rec_texts = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "section"
        and b.get("text")
        and "Top recommendations" in b["text"]["text"]
    ]
    assert rec_texts
    rec_text = rec_texts[0]
    # It should not contain raw Python list/dict markers at top-level
    assert not rec_text.strip().startswith("[")
    assert "check_summary" not in rec_text.lower()
