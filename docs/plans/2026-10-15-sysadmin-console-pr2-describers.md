---
title: "Sysadmin Console PR 2: Describers"
parent: Plans
---

# Sysadmin console PR 2: describers

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Every checker result, alert and incident has a human description rendered on read, and `Incident.title` and `Incident.description` are gone.

**Architecture:** `config/describing.py` holds the `Description`, `Action` and `Fact` types and the formatters. Checkers describe their own results through `BaseChecker.describe`. `apps/alerts/describing.py` describes alerts and incidents, using the hub's checker classes for checker alerts. Every reader of the two dropped columns switches to the incident describer, then a migration drops them.

**Tech Stack:** Django 5.2, pytest-django, dataclasses.

Design: `2026-10-15-sysadmin-console-foundation-design.md`, sections 1, 2 and 5 step 2.

## Scope notes

- Actions carry text only, except a runbook link (`intent="open_url"`). The intent table is
  PR 5, so nothing here invents intents the console can't resolve yet.
- Console pages keep their read models and templates. Only the values feeding `title` and
  `description` change. The console retrofit is PR 5.
- Notify keeps its current context. `incident_title` now comes from the describer. The
  template rebuild is PR 4.
- `apps/console/formatting.py` moves into `config/describing.py` as `format_duration`, so
  there's one duration formatter.

## Conventions for every task

- TDD: failing test, run it, implement, run it, commit.
- 100% branch coverage on changed code:
  `uv run coverage run -m pytest <paths> && uv run coverage report --include=<files> -m`.
- Commit through pre-commit with a 600000ms timeout. Never `--no-verify`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em or en dashes in code, strings or docs. Comments only for non-obvious constraints.
- Headlines and details never contain a UUID, a `snake_case` token or a `{`.

---

### Task 1: The describing contract and formatters

**Files:**
- Create: `config/describing.py`
- Create: `config/_tests/test_describing.py`
- Delete: `apps/console/formatting.py`, `apps/console/_tests/test_formatting.py`
- Modify: `apps/console/queries/incident.py` (import `format_duration` from `config.describing`)

**Step 1: Write the failing tests** in `config/_tests/test_describing.py`:

```python
import pytest

from config.describing import (
    Action,
    Description,
    Fact,
    facts_from,
    format_duration,
    format_list,
    format_percent,
    format_size_mb,
)


def test_description_defaults():
    d = Description(headline="CPU at 94%", tone="critical")
    assert (d.detail, d.action, d.facts) == ("", None, ())


def test_description_rejects_an_unknown_tone():
    with pytest.raises(ValueError, match="tone"):
        Description(headline="x", tone="red")


def test_description_rejects_an_empty_headline():
    with pytest.raises(ValueError, match="headline"):
        Description(headline="", tone="ok")


def test_action_and_fact_defaults():
    assert Action("Restart nginx") == Action(text="Restart nginx", intent="", params={})
    assert Fact("Host", "web-01").copy is False


@pytest.mark.parametrize(
    "value,expected", [(94, "94%"), (94.0, "94%"), (94.25, "94.2%"), (0, "0%"), (99.96, "100%")]
)
def test_format_percent(value, expected):
    assert format_percent(value) == expected


@pytest.mark.parametrize(
    "mb,expected",
    [(0, "0 MB"), (512.4, "512 MB"), (1024, "1.0 GB"), (17544.1, "17.1 GB"), (3 * 1024**2, "3.0 TB")],
)
def test_format_size_mb(mb, expected):
    assert format_size_mb(mb) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0s"), (0.25, "250ms"), (59, "59s"), (61, "1m 01s"), (3700, "1h 01m"), (90000, "1d 1h")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "items,expected",
    [
        ([], ""),
        (["a"], "a"),
        (["a", "b"], "a and b"),
        (["a", "b", "c"], "a, b and c"),
        (["a", "b", "c", "d", "e"], "a, b, c and 2 more"),
    ],
)
def test_format_list(items, expected):
    assert format_list(items) == expected


def test_facts_from_keeps_scalars_and_skips_nested_values():
    facts = facts_from({"cpu_percent": 94.2, "per_cpu": [1, 2], "ok": True, "gone": None})
    assert facts == (Fact("Cpu percent", "94.2"), Fact("Ok", "True"))
```

Move every case from `apps/console/_tests/test_formatting.py` into `test_format_duration`
if it isn't already covered above.

**Step 2:** `uv run pytest config/_tests/test_describing.py -q`. Expected: ImportError.

**Step 3: Implement** `config/describing.py`:

```python
"""What an app's output means, in words a sysadmin can act on.

Each app describes its own output with these types. The console and notifications render
them and never decode raw rows themselves. Describers return an intent, never a URL, so
nothing upstream imports ``apps.console``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from config.admin_badges import TONES


@dataclass(frozen=True)
class Action:
    text: str
    intent: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    copy: bool = False


@dataclass(frozen=True)
class Description:
    headline: str
    tone: str
    detail: str = ""
    action: Action | None = None
    facts: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        if self.tone not in TONES:
            raise ValueError(f"unknown tone: {self.tone!r}")
        if not self.headline:
            raise ValueError("a description needs a headline")


def format_percent(value: float) -> str:
    return f"{round(float(value), 1):g}%"


def format_size_mb(mb: float) -> str:
    if mb < 1024:
        return f"{mb:.0f} MB"
    if mb < 1024**2:
        return f"{mb / 1024:.1f} GB"
    return f"{mb / 1024**2:.1f} TB"


def format_duration(seconds: float) -> str:
    # body of apps/console/formatting.py humanize_duration, unchanged


def format_list(items: Iterable[str], limit: int = 3) -> str:
    items = list(items)
    if len(items) > limit:
        return f"{', '.join(items[:limit])} and {len(items) - limit} more"
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def facts_from(metrics: Mapping[str, Any]) -> tuple[Fact, ...]:
    return tuple(
        Fact(key.replace("_", " ").capitalize(), str(value))
        for key, value in metrics.items()
        if isinstance(value, (str, int, float, bool))
    )
```

Check `format_percent(99.96)`: `round(99.96, 1)` is `100.0`, and `:g` gives `100`. Delete
`apps/console/formatting.py` and its test, and repoint the one import in
`apps/console/queries/incident.py`.

**Step 4:** `uv run pytest config/_tests/test_describing.py apps/console -q`. Expected: pass.

**Step 5:** Commit `feat(config): a describing contract every app renders its output through`.

---

### Task 2: The checker describing contract

**Files:**
- Modify: `apps/checkers/checkers/base.py`
- Create: `apps/checkers/_tests/checkers/test_describe_contract.py`

**Step 1: Write the failing tests.**

```python
import re

import pytest

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from config.describing import Action

RAW = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b|[0-9a-f]{8}-[0-9a-f]{4}-|\{")


class Plain(BaseChecker):
    name = "plain"
    label = "Plain thing"
    problem = "Plain thing broken"

    def check(self) -> CheckResult:  # pragma: no cover
        raise NotImplementedError


class Broken(Plain):
    def describe_result(self, status, metrics):
        return self._describe(status, f"Value {metrics['missing']}", metrics)


def test_default_describe_falls_back_to_the_message():
    d = Plain().describe(CheckStatus.WARNING, {"load": 3}, "Load is 3")
    assert (d.headline, d.tone) == ("Load is 3", "warning")
    assert [f.label for f in d.facts] == ["Load"]


def test_default_describe_without_a_message_names_the_check():
    assert Plain().describe(CheckStatus.OK, {}).headline == "Plain thing: ok"


def test_skipped_reads_as_not_checked():
    d = Plain().describe(CheckStatus.SKIPPED, {}, "Skipped: not Linux")
    assert (d.headline, d.tone, d.action) == ("Plain thing not checked: not Linux", "muted", None)


def test_unknown_reads_as_could_not_check_with_the_error_as_detail():
    d = Plain().describe(CheckStatus.UNKNOWN, {}, "Check failed: boom")
    assert d.headline == "Plain thing could not be checked"
    assert d.detail == "Check failed: boom"
    assert d.action == Action("Run this check by hand on the host to see the error.")


def test_metrics_a_describer_cannot_read_fall_back_to_the_message():
    assert Broken().describe(CheckStatus.CRITICAL, {}, "raw").headline == "raw"


def test_ok_results_carry_no_action():
    d = Plain()._describe(CheckStatus.OK, "Fine", {}, action=Action("Do it"))
    assert d.action is None


@pytest.mark.parametrize("name,cls", sorted(CHECKER_REGISTRY.items()))
def test_every_checker_has_a_label_and_a_problem(name, cls):
    assert cls.label and cls.problem and cls.label != cls.problem
    assert not RAW.search(cls.label) and not RAW.search(cls.problem)


@pytest.mark.parametrize("name,cls", sorted(CHECKER_REGISTRY.items()))
@pytest.mark.parametrize("status", list(CheckStatus))
def test_every_checker_describes_empty_metrics_in_words(name, cls, status):
    d = cls().describe(status, {}, "")
    assert d.headline and not RAW.search(d.headline)
```

The last two guard tests fail until Tasks 3 to 5 land. Mark them
`@pytest.mark.xfail(strict=True, reason="checker describers land in tasks 3-5")` here, and
remove the marker in Task 5.

**Step 2:** Run the file. Expected: AttributeError on `describe`.

**Step 3: Implement** on `BaseChecker`:

```python
    label: str = ""
    problem: str = ""

    def describe(
        self, status: CheckStatus, metrics: dict[str, Any], message: str = ""
    ) -> Description:
        if status is CheckStatus.SKIPPED:
            reason = message.removeprefix("Skipped:").strip() or "not applicable here"
            return self._describe(status, f"{self.label} not checked: {reason}", metrics)
        if status is CheckStatus.UNKNOWN:
            return self._describe(
                status,
                f"{self.label} could not be checked",
                metrics,
                detail=message,
                action=Action("Run this check by hand on the host to see the error."),
            )
        try:
            found = self.describe_result(status, metrics)
        # Metrics come from nodes on older versions too, so a missing or odd key falls
        # back to the node's own message instead of failing the page.
        except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
            found = None
        if found is not None:
            return found
        return self._describe(status, message or f"{self.label}: {status.value}", metrics)

    def describe_result(self, status: CheckStatus, metrics: dict[str, Any]) -> Description | None:
        return None

    def _describe(
        self,
        status: CheckStatus,
        headline: str,
        metrics: dict[str, Any],
        *,
        detail: str = "",
        action: Action | None = None,
    ) -> Description:
        acts = status in (CheckStatus.WARNING, CheckStatus.CRITICAL, CheckStatus.UNKNOWN)
        return Description(
            headline=headline,
            tone=CHECK_STATUS_TONES[status.value],
            detail=detail,
            action=action if acts else None,
            facts=facts_from(metrics),
        )
```

Import `CHECK_STATUS_TONES` from `config.admin_badges`, and `Action`, `Description` and
`facts_from` from `config.describing`. `describe_result` sees only OK, WARNING and CRITICAL.

**Step 4:** Run the file. Expected: pass, with the two guards xfail.

**Step 5:** Commit `feat(checkers): a checker describes its own result`.

---

### Tasks 3 to 5: Checker describers

Each checker sets `label`, `problem` and `describe_result`. Each gets tests in its
existing `apps/checkers/_tests/checkers/test_<name>.py`: one per status branch, using
metrics shaped exactly like that checker's `check()` output, plus one asserting that
`describe` on that checker's real `check()` output (with psutil mocked as the file already
does) returns a headline matching no `RAW` pattern.

Wording rules: the headline states the measured fact, and the action is a step the
operator takes. Use `format_percent`, `format_size_mb` and `format_list` for numbers and
lists. Read metrics with `metrics["key"]` for required keys, so a missing key falls back
through Task 2's guard.

Worked example, `cpu.py`:

```python
    label = "CPU usage"
    problem = "High CPU usage"

    def describe_result(self, status, metrics):
        percent = format_percent(metrics["cpu_percent"])
        if status is CheckStatus.OK:
            cores = metrics.get("cpu_count")
            return self._describe(
                status, f"CPU at {percent} across {cores} cores" if cores else f"CPU at {percent}", metrics
            )
        return self._describe(
            status,
            f"CPU at {percent}",
            metrics,
            action=Action("Find the busiest processes with top, sorted by CPU, and stop or limit the runaway one."),
        )
```

#### Task 3: cpu, memory, cpu_temp, disk_temp

| Checker | label | problem | OK headline | WARNING / CRITICAL headline | Action |
|---|---|---|---|---|---|
| cpu | CPU usage | High CPU usage | `CPU at 41% across 8 cores` | `CPU at 94%` | as above |
| memory | Memory usage | High memory usage | `Memory at 41%, 9.4 GB free of 16.0 GB` | same shape | `Find the largest processes with top, sorted by memory, and restart or limit them.` |
| cpu_temp | CPU temperature | CPU running hot | `Hottest sensor Package id 0 at 52°C` | same shape | `Check the fans and airflow, and clean dust from the heatsink.` |
| disk_temp | Disk temperature | Disk running hot | `Hottest disk nvme0 at 41°C` | same shape | `Check the airflow around the disks. A disk that stays hot fails sooner.` |

memory: `memory_percent`, `memory_available_gb`, `memory_total_gb`, sizes through
`format_size_mb(gb * 1024)`. Temps: `hottest_c` and `hottest_sensor` or `hottest_disk`,
rounded to whole degrees. A temp checker with `hottest_c` of `None` raises `TypeError` on
purpose, which falls back.

Commit: `feat(checkers): describe cpu, memory and temperature results`.

#### Task 4: disk, disk_inodes, io_strain, disk analyzers

| Checker | label | problem | OK headline | WARNING / CRITICAL headline | Action |
|---|---|---|---|---|---|
| disk | Disk space | Disk filling up | `Fullest disk / at 41%, 120.0 GB free` | `/ is 94% full, 3.2 GB free` | `Free space on /: clear old logs, caches and packages, or grow the volume.` |
| disk_inodes | Inode usage | Running out of inodes | `Fullest filesystem / uses 12% of its inodes` | `/ uses 94% of its inodes` | `Find directories under / holding many small files and remove what isn't needed.` |
| io_strain | Disk I/O | Disk I/O saturated | `Busiest disk sda at 12% utilization` | `sda busy 94% of the time` | `Find what is reading or writing with iotop, then move or throttle it.` |
| disk_common | Reclaimable disk space | Disk space to reclaim | `1.2 GB could be reclaimed` | same shape | first line of `recommendations[0]`, else `Review the largest paths in Details and remove what isn't needed.` |
| disk_linux | Reclaimable disk space on Linux | Linux disk space to reclaim | same | same | same |
| disk_macos | Reclaimable disk space on macOS | macOS disk space to reclaim | same | same | same |

disk: `worst_path`, `worst_percent`, `disks[worst_path]["free_gb"]`. disk_inodes:
`worst_path`, `worst_percent`; `disk_inodes` with no inode-tracking filesystem has
`worst_path` of `None`, so return `self._describe(status, "No filesystems track inodes", metrics)`
for that case. io_strain: `busiest_disk`, `busiest_util_percent`. The three analyzers share
one `describe_result` on `BaseDiskAnalyzer` reading `total_recoverable_mb` and
`recommendations`; subclasses set only `label` and `problem`.

Commit: `feat(checkers): describe disk, inode, I/O and cleanup results`.

#### Task 5: network, process, listening_ports, raid, reboot_debian

| Checker | label | problem | OK headline | WARNING / CRITICAL headline | Action |
|---|---|---|---|---|---|
| network | Network reachability | Hosts unreachable | `All 3 hosts reachable` | `2 of 3 hosts unreachable: 8.8.8.8 and 1.1.1.1` | `Check the route and firewall between this machine and 8.8.8.8 and 1.1.1.1.` |
| process | Watched processes | Processes not running | `All 2 watched processes running`, or `No processes to watch` when `total_count` is 0 | `nginx and redis not running` | `Start nginx and redis and read their logs for why they stopped.` |
| listening_ports | Listening ports | Unexpected open ports | `12 ports listening, all expected` | `Unexpected ports open: 8080 and 9000` | `Close 8080 and 9000, or add them to this node's port allowlist.` |
| raid | RAID arrays | RAID array degraded | `3 RAID arrays healthy`, or `No software RAID arrays` when `array_count` is 0 | CRITICAL `Degraded: md0`, WARNING `Rebuilding: md1` | CRITICAL `Replace the failed disk in md0 and add it back with mdadm.`, WARNING `Let md1 finish rebuilding and avoid heavy writes until it does.` |
| reboot_debian | Pending reboot | Reboot required | `No reboot required` | `Reboot required by 4 updated packages` | `Schedule a reboot in a maintenance window.` |

network: hosts are `metrics["hosts"]` keys whose `reachable` is false. process: names are
`metrics["processes"]` keys whose `running` is false. listening_ports: `unexpected_ports`.
raid: `degraded_arrays`, `rebuilding_arrays`. reboot_debian: `pending_package_count`.
Lists go through `format_list`.

Remove the `xfail` markers from Task 2's guards. Run
`uv run pytest apps/checkers -q`. Expected: pass with no xfail.

Commit: `feat(checkers): describe network, process, port, RAID and reboot results`.

---

### Task 6: Checker alerts are named by their label

**Files:**
- Modify: `apps/alerts/check_integration.py` (`name=` in the `ParsedAlert`)
- Modify: `apps/alerts/management/commands/push_to_hub.py` (`"name"` in `_result_to_alert`, and the comment above it, which claims the name must match for identity)
- Modify: tests asserting `"<NAME> Check Alert"` in `apps/alerts/_tests/` and `apps/orchestration/_tests/` (list: `grep -rn "Check Alert" apps --include=*.py`)

**Step 1:** Change the bridge and push tests to expect the label: a CPU result is named
`CPU usage`. Add one test each: an unregistered checker name `zfs_pool` is named `Zfs pool`.

**Step 2:** Run them. Expected: fail.

**Step 3:** Add `checker_label(name: str) -> str` to `apps/checkers/checkers/__init__.py`:
the registry class's `label`, else `name.replace("_", " ").capitalize()`. Both producers use
it. The push comment becomes one line: identity is the fingerprint and subject, so the name
is display only.

**Step 4:** `uv run pytest apps/alerts apps/orchestration -q`. Expected: pass. Leave
`test_subject_migration.py` alone, since it tests historical rows.

**Step 5:** Commit `feat(alerts): name checker alerts by what they check`.

---

### Task 7: Alert and incident describers

**Files:**
- Create: `apps/alerts/describing.py`
- Create: `apps/alerts/_tests/test_describing.py`

**Step 1: Write the failing tests.** Build real rows. Cases:

1. A firing checker alert, `labels={"checker": "cpu", "instance_id": "i-1"}`,
   `raw_payload={"status": "critical", "metrics": {"cpu_percent": 94.0}}`, severity
   critical: `describe_alert` headline `CPU at 94%`, tone `critical`.
2. The same alert with no `raw_payload` metrics falls back to `alert.description`.
3. `raw_payload["status"] == "unknown"` describes as `CPU usage could not be checked`.
4. A resolved checker alert describes with OK status and no action.
5. An unregistered checker `zfs_pool` uses the first line of its description as the
   headline, and carries `Fact("Described by", "the node")`.
6. A webhook alert named `HighLatency` with
   `annotations={"runbook_url": "https://r.example/x"}` has headline `HighLatency`, its
   description as detail, and `Action("Open the runbook", "open_url", {"url": "https://r.example/x"})`.
   A `javascript:` runbook value gives no action.
7. `describe_incident` on an incident with a node `hostname="web-01"` and a critical cpu
   alert and a warning memory alert: headline `High CPU usage on web-01`, detail
   `CPU at 94%`, the cpu alert's action, tone `critical`. Facts start with
   `Fact("Incident", "#<pk>", copy=True)`, `Fact("Subject", "checker:cpu")`,
   `Fact("Instance", "i-1")` and include the fingerprint with `copy=True`.
8. No node: the host is `incident.instance`. No instance either: the headline has no
   ` on ` part.
9. Every alert resolved: the newest alert leads, and the tone is `ok`.
10. No alerts at all: headline `Incident #<pk>`.
11. A webhook incident: headline `HighLatency on db-1`, detail is the alert's description.
12. `describe_incidents` over five incidents runs a fixed number of queries
    (`django_assert_num_queries(2)`: alerts, nodes).

**Step 2:** Run. Expected: ImportError.

**Step 3: Implement.**

```python
"""Alerts and incidents in words, rendered on read from their stored keys.

A checker alert is described by the hub's own checker class from the metrics it carried,
so a node can't put words in the hub's mouth. Webhook alerts keep the name and text their
source gave, which is already written for people.
"""

from collections import defaultdict
from collections.abc import Iterable

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, Node
from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus, checker_label
from config.admin_badges import SEVERITY_TONES, tone_for
from config.describing import Action, Description, Fact

_RANK = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _checker_name(alert: Alert) -> str:
    name = _dict(alert.labels).get("checker")
    return name if isinstance(name, str) else ""


def check_status_of(alert: Alert) -> CheckStatus:
    if _dict(alert.raw_payload).get("status") == CheckStatus.UNKNOWN.value:
        return CheckStatus.UNKNOWN
    if alert.status == AlertStatus.RESOLVED:
        return CheckStatus.OK
    if alert.severity == AlertSeverity.CRITICAL:
        return CheckStatus.CRITICAL
    return CheckStatus.WARNING


def _runbook(alert: Alert) -> Action | None:
    annotations = _dict(alert.annotations)
    url = annotations.get("runbook_url") or annotations.get("runbook")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return Action("Open the runbook", intent="open_url", params={"url": url})
    return None


def describe_alert(alert: Alert) -> Description:
    name = _checker_name(alert)
    checker = CHECKER_REGISTRY.get(name)
    if checker is not None:
        metrics = _dict(_dict(alert.raw_payload).get("metrics"))
        return checker().describe(check_status_of(alert), metrics, alert.description)
    firing = alert.status == AlertStatus.FIRING
    tone = tone_for(SEVERITY_TONES, alert.severity) if firing else "ok"
    if name:
        first = alert.description.strip().splitlines()[0] if alert.description.strip() else ""
        return Description(
            headline=first or checker_label(name),
            tone=tone,
            detail=alert.description,
            facts=(Fact("Described by", "the node"),),
        )
    return Description(
        headline=alert.name or "Unnamed alert",
        tone=tone,
        detail=alert.description,
        action=_runbook(alert),
    )


def _lead(alerts: list[Alert]) -> Alert | None:
    pool = [a for a in alerts if a.status == AlertStatus.FIRING] or alerts
    return min(pool, key=lambda a: _RANK.get(a.severity, 3)) if pool else None


def _describe(incident: Incident, alerts: list[Alert], node: Node | None) -> Description:
    host = (node.hostname or node.instance_id) if node is not None else incident.instance
    tone = "ok" if incident.is_resolved else tone_for(SEVERITY_TONES, incident.severity)
    facts = (
        Fact("Incident", f"#{incident.pk}", copy=True),
        Fact("Subject", incident.subject),
        Fact("Instance", incident.instance),
    )
    lead = _lead(alerts)
    if lead is None:
        headline = f"Incident #{incident.pk}"
        return Description(headline=f"{headline} on {host}" if host else headline, tone=tone, facts=facts)
    finding = describe_alert(lead)
    name = _checker_name(lead)
    checker = CHECKER_REGISTRY.get(name)
    what = checker.problem if checker else (checker_label(name) if name else finding.headline)
    return Description(
        headline=f"{what} on {host}" if host else what,
        tone=tone,
        detail=finding.headline if name else finding.detail,
        action=finding.action,
        facts=facts + (Fact("Fingerprint", lead.fingerprint, copy=True),) + finding.facts,
    )


def describe_incidents(incidents: Iterable[Incident]) -> dict[int, Description]:
    incidents = list(incidents)
    by_incident: defaultdict[int, list[Alert]] = defaultdict(list)
    for alert in Alert.objects.filter(incident_id__in=[i.pk for i in incidents]).order_by(
        "-received_at", "-pk"
    ):
        by_incident[alert.incident_id].append(alert)
    nodes = Node.objects.in_bulk({i.node_id for i in incidents if i.node_id})
    return {i.pk: _describe(i, by_incident[i.pk], nodes.get(i.node_id)) for i in incidents}


def describe_incident(incident: Incident) -> Description:
    return describe_incidents([incident])[incident.pk]
```

`min` keeps the first of equal ranks, and alerts arrive newest first, so the newest of the
worst severity leads. An empty `in_bulk` set still runs no query, so in case 12 assert the
count that matches the fixture (nodes present gives 2).

**Step 4:** Run with coverage on `apps/alerts/describing.py`. Expected: 100%.

**Step 5:** Commit `feat(alerts): describe alerts and incidents from their keys`.

---

### Task 8: Every reader moves to the describer

The columns still exist. Replace each read of `Incident.title` or `Incident.description`.
Update each site's tests in the same commit.

| Site | Becomes |
|---|---|
| `apps/alerts/models.py` `Incident.__str__` | `f"Incident #{self.pk} [{self.status}]"` |
| `apps/alerts/services.py` log lines (`Created`, `Auto-resolved`, `acknowledged`, `resolved`, `closed`, `{done}`) | `#{incident.pk}` with `subject` and `instance`, e.g. `"Created incident #%s (%s on %s)"` using lazy `%s` logging args |
| `apps/alerts/services.py` operator message `Incident '{title}' {done}.` | `f"Incident #{incident.pk} {done}."` |
| `apps/alerts/node_overview.py` `IncidentRow(title=...)` | `describe_incidents(incidents)[pk].headline` |
| `apps/alerts/admin.py` Incident `list_display`, `search_fields`, fieldsets | a `headline` display method from `describe_incident`; search on `subject`, `instance`; fieldset shows `headline` read-only and drops `description` |
| `apps/alerts/admin.py` Alert `incident` and journey columns (`obj.incident.title[:30]`, `[:40]`) | `describe_incident(obj.incident).headline` truncated the same way |
| `apps/intelligence/providers/ai_base.py` `_build_prompt` | `d = describe_incident(incident)`; lines `Incident: {d.headline}` and `Detail: {d.detail}` when detail is set |
| `apps/intelligence/providers/local.py` incident type detection | start the text from `incident.subject`, then the alerts loop as now |
| `apps/intelligence/management/commands/get_recommendations.py` | `describe_incident(incident).headline` |
| `apps/orchestration/executors.py` `result.incident_title` and `_headline_facts` | `describe_incident(incident).headline`; drop the `incident.title` truthiness check |
| `apps/orchestration/management/commands/trace.py` | `"title": describe_incident(incident).headline` |
| `apps/console/queries/incident.py` | `title=d.headline`, `description=d.detail` |
| `apps/console/queries/incidents.py`, `apps/console/queries/overview.py` | one `describe_incidents(found)` call per page, `title=` from it |

Intelligence provider tests build `MagicMock` incidents. Give them `subject` and patch
`describe_incident` where `_build_prompt` is under test, through one fixture in the
providers' `conftest.py` (create it if missing).

After this task, `grep -rnE "incident\w*\.(title|description)\b" apps config --include=*.py | grep -v _tests`
returns nothing.

Run `uv run pytest -q` (full suite). Commit `refactor: read incident headlines from the describer`.

---

### Task 9: Drop the columns

**Files:**
- Modify: `apps/alerts/models.py` (remove `title`, `description` from `Incident`)
- Create: `apps/alerts/migrations/0013_drop_incident_title_description.py` via `uv run python manage.py makemigrations alerts -n drop_incident_title_description`
- Modify: `apps/alerts/services.py` `Incident.objects.create(...)` (drop the two kwargs)
- Modify: every test passing `title=` or `description=` to `Incident.objects.create`

**Step 1:** Remove the fields and generate the migration. Run `black` on it.

**Step 2:** Strip the kwargs from test fixtures with a script that edits only inside
`Incident.objects.create(` and `Incident.objects.get_or_create(` calls. Write it to the
scratchpad, not the repo. Then fix what's left by hand: tests that asserted on an
incident's title now assert on `describe_incident(...).headline` or on `subject`.

**Step 3:** `uv run python manage.py makemigrations --check --dry-run` reports no changes.
`uv run python manage.py migrate` on the local DB succeeds.

**Step 4:** `uv run pytest -q`. Expected: pass.

**Step 5:** Commit `feat(alerts)!: drop Incident.title and description`.

---

### Task 10: Docs

- `AGENTS.md`: under conventions, add `config/describing.py`: apps describe their own
  output on read, and nothing stores display text. Remove any mention of the incident title.
- `apps/checkers/AGENTS.md`: the `label`, `problem` and `describe_result` contract, the
  fallback rule, and the guard tests.
- `apps/alerts/AGENTS.md`: `describe_alert`, `describe_incident` and `describe_incidents`;
  checker alerts are named by label; the incident has no stored title.
- `apps/intelligence/AGENTS.md` if it names the incident title in the prompt.
- `docs/Architecture.md`: one paragraph on describers.
- Don't touch `docs/plans/`.

Commit `docs: apps describe their own output; incidents have no stored title`.

---

## Acceptance

- `uv run pytest -q` passes. Changed files have 100% branch coverage.
- `grep -rnE "\.title\b" apps/alerts/models.py` finds nothing on `Incident`.
- `uv run python manage.py check_health` and `uv run python manage.py run_pipeline --sample --dry-run` run clean.
- On the local DB, every incident's `describe_incident(...).headline` matches no `RAW`
  pattern. Check with a one-off shell loop and report the output.
