"""
Formatting helpers for notification stage summaries.

Extracts markdown-rendering logic from NotifyExecutor so it can be
tested independently and keeps the executor focused on orchestration.
"""

from typing import Any


def format_ingest_summary(ingest_prev: dict[str, Any]) -> str:
    """Build a Markdown summary from ingest stage output."""
    if not isinstance(ingest_prev, dict):
        return "**Ingest Summary**\n```\n" + str(ingest_prev) + "\n```"

    lines = ["**Ingest Summary**"]
    lines.append(f"- incident_id: `{ingest_prev.get('incident_id')}`")
    lines.append(f"- severity: {ingest_prev.get('severity')}")
    lines.append(f"- source: {ingest_prev.get('source')}")
    lines.append(f"- alerts_created: {ingest_prev.get('alerts_created', 0)}")
    lines.append(f"- alerts_updated: {ingest_prev.get('alerts_updated', 0)}")
    lines.append(f"- alerts_resolved: {ingest_prev.get('alerts_resolved', 0)}")
    lines.append(f"- incidents_created: {ingest_prev.get('incidents_created', 0)}")
    lines.append(f"- incidents_updated: {ingest_prev.get('incidents_updated', 0)}")
    lines.append(f"- duration_ms: `{round(float(ingest_prev.get('duration_ms', 0.0)), 2)}`")
    return "\n".join(lines)


def format_check_summary(check_prev: dict[str, Any]) -> str:
    """Build a Markdown summary from check stage output."""
    if not isinstance(check_prev, dict):
        return "**Check Summary**\n```\n" + str(check_prev) + "\n```"

    lines = ["**Check Summary**"]
    lines.append(f"- checks_run: {check_prev.get('checks_run', 0)}")
    lines.append(f"- passed: {check_prev.get('checks_passed', 0)}")
    lines.append(f"- failed: {check_prev.get('checks_failed', 0)}")
    cof = check_prev.get("checker_output_ref")
    if cof:
        lines.append(f"- checker_output_ref: `{cof}`")
    lines.append(f"- duration_ms: `{round(float(check_prev.get('duration_ms', 0.0)), 2)}`")
    return "\n".join(lines)


def format_intelligence_summary(intelligence_prev: dict[str, Any]) -> str:
    """Build a Markdown summary from intelligence stage output."""
    if not isinstance(intelligence_prev, dict):
        return "**Intelligence Summary**\n```\n" + str(intelligence_prev) + "\n```"

    lines = ["**Intelligence Summary**"]
    if intelligence_prev.get("summary"):
        lines.append(f"- summary: {intelligence_prev.get('summary')}")
    if intelligence_prev.get("probable_cause"):
        lines.append(f"- probable_cause: {intelligence_prev.get('probable_cause')}")
    recs = intelligence_prev.get("recommendations") or []
    lines.append(f"- recommendations: {len(recs)}")

    top_proc_lines: list[str] = []
    if recs and isinstance(recs, list) and isinstance(recs[0], dict):
        details = recs[0].get("details", {}) or {}
        top = details.get("top_processes") or []
        if isinstance(top, list) and top:
            top_proc_lines.append("- top_processes:")
            for p in top[:5]:
                pid = p.get("pid")
                name = p.get("name") or (p.get("cmdline") or "")
                cpu = p.get("cpu_percent")
                try:
                    cpu_s = f"{float(cpu):.1f}%" if cpu is not None else ""
                except Exception:
                    cpu_s = str(cpu)
                top_proc_lines.append(f"  - `{pid}` {name} — {cpu_s}")
    return "\n".join(lines + top_proc_lines)


def build_notification_body(
    message_body: str,
    ingest_md: str,
    check_md: str,
    intel_md: str,
) -> str:
    """Join non-empty section strings with horizontal rules."""
    parts = []
    if message_body:
        parts.append(message_body)
    if ingest_md:
        parts.append(ingest_md)
    if check_md:
        parts.append(check_md)
    if intel_md:
        parts.append(intel_md)
    return "\n\n---\n\n".join(parts).strip()
