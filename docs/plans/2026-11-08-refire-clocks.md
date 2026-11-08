---
title: "Refire Clocks Plan"
parent: Plans
---

{% raw %}
# Refire Clocks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A refired incident reads from its latest fire: header, duration, list clock, sort
and window all use a new `Incident.opened_at`, and the alert page says when it fired again.

**Architecture:** One timestamp on `Incident`, set at create and by `reopen()`. Console
queries read it. The alert's "fired again" time is derived from its newest `refired`
`AlertHistory` row. Design: `docs/plans/2026-11-08-refire-clocks-design.md`.

**Tech Stack:** Django 5.2 models and migrations, console queries and templates, pytest.

---

### Task 1: `Incident.opened_at`

**Files:**
- Modify: `apps/alerts/models.py` (`Incident` timestamps, `reopen()`)
- Create: `apps/alerts/migrations/0014_incident_opened_at.py`
- Test: `apps/alerts/_tests/test_models.py`, `apps/alerts/_tests/test_migrations.py` if one
  exists for data migrations, otherwise a test next to the model tests

**Step 1: failing tests**

```python
def test_a_new_incident_opens_when_it_is_created(db):
    incident = Incident.objects.create(subject="cpu")
    assert incident.opened_at is not None
    assert abs(incident.opened_at - incident.created_at) < timedelta(seconds=1)


def test_reopen_moves_opened_at_and_keeps_created_at(db):
    incident = Incident.objects.create(subject="cpu")
    created = incident.created_at
    incident.resolve()
    later = timezone.now() + timedelta(hours=1)
    with freeze(later):  # use the clock helper the alerts tests already use
        incident.reopen()
    incident.refresh_from_db()
    assert incident.opened_at == later
    assert incident.created_at == created
```

**Step 2:** run them, expect FAIL (no field).

**Step 3: implement**

```python
opened_at = models.DateTimeField(
    default=timezone.now,
    help_text="When the current episode began: creation, or the latest reopen.",
)
```

In `reopen()`: set `self.opened_at = timezone.now()` and add `"opened_at"` to
`update_fields`. Add `models.Index(fields=["opened_at"])`.

Migration: `uv run python manage.py makemigrations alerts -n incident_opened_at`, then add a
`RunPython` that runs `Incident.objects.update(opened_at=F("created_at"))`, with
`RunPython.noop` as the reverse. Check `black` vs pre-commit on the migration file (see the
testing gotchas: they disagree on migrations, so run pre-commit's black on it).

**Step 4:** run the tests, expect PASS. Also run
`uv run pytest apps/alerts -q -p no:cacheprovider`.

**Step 5:** commit `feat(alerts): an incident records when its current episode opened`.

### Task 2: refire through the write path moves `opened_at`

**Files:**
- Test: `apps/alerts/_tests/test_services.py` (or wherever `AlertOrchestrator` refire and
  reopen tests live; grep for `incident.reopen` and `"refired"`)

**Step 1:** a test that fires an alert, resolves it and its incident, advances the clock and
fires the same fingerprint again through `AlertOrchestrator`. Assert the incident is open,
`opened_at` equals the later time and `created_at` is unchanged.

**Step 2:** it should already pass through `reopen()`. If it doesn't, find the reopen path
that bypasses `Incident.reopen()` and route it through. Commit only if a code change was
needed, otherwise fold the test into the next commit.

### Task 3: incident page

**Files:**
- Modify: `apps/console/queries/incident.py` (`IncidentDetail`, `build_incident_detail`,
  `_lifecycle_entries`)
- Modify: `templates/console/partials/_incident.html` (page head meta)
- Test: `apps/console/_tests/views/test_incident.py`, `apps/console/_tests/queries/` incident
  tests

**Step 1: failing tests**

- A never-reopened incident: header shows "Started" only, no "Fired again", duration from
  `created_at`, no `entry-incident-reopened` in the timeline.
- A reopened incident (`created_at` 3 weeks ago, `opened_at` 5 minutes ago, open): header
  shows "Fired again" with a `<time>` for `opened_at`, duration reads 5 minutes "so far",
  the timeline has `id="entry-incident-reopened"` titled "Incident fired again".

**Step 3: implement**

- `IncidentDetail` gains `opened_at: datetime` and `reopened: bool`
  (`incident.opened_at > incident.created_at`; compare with a one-second tolerance only if a
  test shows creation stamps differ, otherwise strict).
- `elapsed = (ended_at or timezone.now()) - incident.opened_at`.
- `_lifecycle_entries`: after the `LIFECYCLE` loop, when reopened, append an entry
  `id="entry-incident-reopened"`, `when=incident.opened_at`, `Description("Incident fired
  again", tone_for(INCIDENT_STATUS_TONES, "open"))`.
- Template: after the "Started" span add
  `{% if incident.reopened %}<span>Fired again {% include "console/partials/_time.html" with when=incident.opened_at %}</span>{% endif %}`.

**Step 4:** tests pass. **Step 5:** commit `feat(console): a reopened incident's page reads from its latest fire`.

### Task 4: incident list and overview rows

**Files:**
- Modify: `apps/console/queries/incidents.py` (`IncidentListRow`, `_matching` cutoff,
  `_rows` ordering)
- Modify: `apps/console/queries/overview.py` (`IncidentRow`, `open_incidents` ordering)
- Modify: `templates/console/partials/_incident_row.html`
- Test: `apps/console/_tests/views/test_incidents.py`, `test_overview.py`, their query tests

**Step 1: failing tests**

- Two incidents of equal severity: an old one reopened 1 minute ago and a newer one opened
  1 hour ago. The list puts the reopened one first. A window filter of the last 24 hours
  includes an incident created 30 days ago but reopened today.
- The reopened row reads "fired again" with a `<time>` for `opened_at`. A never-reopened row
  still reads "opened".
- Overview `open_incidents` orders by `opened_at` within rank.
- The overview per-day incident count (around `overview.py:239`) still counts by
  `created_at`: a reopened old incident doesn't add to today.

**Step 3: implement**

- Both row dataclasses gain `opened_at` and `reopened`.
- `_matching`: `filter(opened_at__gte=cutoff)`.
- `_rows`: `order_by("rank", "-opened_at", "-pk")`. `open_incidents`:
  `order_by("rank", "opened_at")`.
- `_incident_row.html`: `#{{ row.pk }} {% if row.reopened %}fired again{% else %}opened{% endif %} {% include ... with when=row.opened_at %}`.
  For a never-reopened row `opened_at == created_at`, so one clock serves both.

**Step 4:** tests pass. **Step 5:** commit `feat(console): incident lists sort and window on the latest fire`.

### Task 5: alert page "Fired again"

**Files:**
- Modify: `apps/console/queries/alert.py` (`AlertDetail` or equivalent, around line 59 and
  157)
- Modify: `templates/console/partials/_alert.html` (page head meta)
- Test: `apps/console/_tests/views/test_alert.py`

**Step 1: failing tests**

- An alert with a `refired` `AlertHistory` row: the header shows "Fired again" with a
  `<time>` for that row's `created_at`. With two `refired` rows, it uses the newest.
- An alert with only a `created` row shows no "Fired again".

**Step 3: implement**

- The detail gains `refired_at: datetime | None`, from
  `alert.history.filter(event="refired").order_by("-created_at", "-pk").values_list("created_at", flat=True).first()`.
- Template: `{% if alert.refired_at %}<span>Fired again {% include "console/partials/_time.html" with when=alert.refired_at %}</span>{% endif %}`
  after "Started".

**Step 4:** tests pass. **Step 5:** commit `feat(console): an alert page says when it fired again`.

### Task 6: docs

- `apps/alerts/AGENTS.md`: one sentence under the incident lifecycle notes:
  `opened_at` is when the current episode began, set at create and by `reopen()`,
  `created_at` is the first fire.
- `apps/console/AGENTS.md`: incident clocks read `opened_at`, the per-day count reads
  `created_at`.

Commit `docs: note what opened_at and created_at mean`.

### Verify

- `uv run pytest apps/alerts apps/console -q -p no:cacheprovider`
- `uv run ruff check .`, `uv run black --check .`, `uv run mypy .`
- Branch coverage on changed files: `uv run coverage run --branch -m pytest apps/alerts apps/console -q -p no:cacheprovider && uv run coverage report --include='apps/alerts/models.py,apps/console/queries/incident.py,apps/console/queries/incidents.py,apps/console/queries/overview.py,apps/console/queries/alert.py'`, then `rm -f .coverage*`.
{% endraw %}
