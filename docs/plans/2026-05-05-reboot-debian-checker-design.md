---
title: "2026-05-05 Reboot Required Checker (Debian Family) Design"
parent: Plans
---

# `reboot_debian` Checker Design

## Problem

After APT installs kernel, libc, systemd, or other reboot-requiring packages, Debian/Ubuntu writes `/var/run/reboot-required` (and a companion `/var/run/reboot-required.pkgs` listing the triggering packages). Until the host reboots, those updates sit unapplied — most commonly leaving an unpatched kernel running. The MOTD surfaces this on SSH login, but the monitoring pipeline currently has no signal: a server can sit pending-reboot indefinitely without anything being raised.

We want a checker that emits an alert as soon as a reboot is required, keeps that alert open until the reboot actually happens, and resolves automatically afterward — using the same alert lifecycle every other checker uses.

## Scope

In scope:
- Detect reboot-required state on Debian, Ubuntu, and Debian-family derivatives (Mint, Pop!_OS, Kali, Raspbian, etc.).
- Surface the list of packages that triggered the reboot requirement.
- Integrate with the existing `CheckAlertBridge` so the alerts layer handles "pending since" semantics, severity persistence, and auto-resolution.

Out of scope:
- RHEL/Fedora support (`needs-restarting -r`) — different mechanism, deferred until needed.
- macOS — no reliable file-based equivalent; `softwareupdate --list` is brittle and slow.
- Parsing customizable MOTD output (`/var/lib/update-notifier/updates-available`) — managed hosts overwrite this with marketing copy.
- Per-checker age-based escalation (WARNING → CRITICAL after N days). If wanted later, this is a system-wide alerts feature ("any open warning past N days → bump severity") that benefits every checker, not a `reboot_debian` concern.

## Approach

### File layout

Mirrors the existing OS-gated checker pattern (`disk_linux`, `disk_macos`):

- New module: `apps/checkers/checkers/reboot_debian.py` — defines `RebootDebianChecker`.
- Registry update: add to `CHECKER_REGISTRY` and exports in `apps/checkers/checkers/__init__.py`.
- New tests: `apps/checkers/_tests/checkers/test_reboot_debian.py`.

No model migrations. No new dependencies. Pure stdlib (`pathlib`, `sys`, `datetime`).

### Class skeleton

```python
class RebootDebianChecker(BaseChecker):
    name = "reboot_debian"
    # Inherited float thresholds are not meaningful for a binary signal;
    # check() sets status directly without consulting them.

    REBOOT_FLAG = Path("/var/run/reboot-required")
    PKGS_FILE = Path("/var/run/reboot-required.pkgs")
    OS_RELEASE = Path("/etc/os-release")
```

The checker overrides `check()` directly rather than relying on `_determine_status` — the same approach `ProcessChecker` uses for its custom severity logic.

### Algorithm

1. If `sys.platform != "linux"`: return OK with `"Skipped: not Linux"` and `platform=<value>`.
2. Parse `/etc/os-release` to determine distro. If missing/unreadable, return OK with `"Skipped: cannot determine distro"`. If not Debian-family, return OK with `"Skipped: not Debian-family (<ID>)"`.
3. If `REBOOT_FLAG` does not exist: return OK with `"No reboot required"`.
4. Read `PKGS_FILE` best-effort (missing or `OSError` → empty list); strip blank lines and whitespace.
5. Return WARNING with `"Reboot required (N pending packages)"` (or `"Reboot required"` when N=0).

The checker is fully stateless. No CheckRun history reads, no file mtime, no persisted "first seen" timestamp.

### Distro detection

```python
def _is_debian_family() -> tuple[bool, str]:
    """Return (is_debian, distro_id). Reads /etc/os-release."""
    if not OS_RELEASE.exists():
        return False, ""
    try:
        fields = {}
        for line in OS_RELEASE.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                fields[k.strip()] = v.strip().strip('"').strip("'")
        distro_id = fields.get("ID", "").lower()
        id_like = fields.get("ID_LIKE", "").lower().split()
        is_debian = distro_id in {"debian", "ubuntu"} or "debian" in id_like
        return is_debian, distro_id
    except OSError:
        return False, ""
```

Catches Debian, Ubuntu, and all derivatives that set `ID_LIKE=debian` (the freedesktop.org spec convention).

### Status mapping

| Condition | Status | Message |
|-----------|--------|---------|
| `sys.platform != "linux"` | OK | "Skipped: not Linux" |
| Linux but `os-release` missing/unreadable | OK | "Skipped: cannot determine distro" |
| Linux but not Debian-family | OK | "Skipped: not Debian-family (`<ID>`)" |
| Debian-family, no flag file | OK | "No reboot required" |
| Debian-family, flag file present | WARNING | `"Reboot required (N pending packages)"` |
| Unexpected exception | UNKNOWN | via `BaseChecker.run()` catch-all |

Status never reaches CRITICAL from this checker. Severity escalation, if wanted, lives at the alerts layer (system-wide concern).

### Metrics shape

```python
{
    "platform": str,                 # sys.platform value
    "distro_id": str,                # "ubuntu" / "debian" / "" if undetected
    "reboot_required": bool,
    "pending_packages": list[str],
    "pending_package_count": int,
}
```

### Edge cases

1. `REBOOT_FLAG` exists but `PKGS_FILE` missing/empty → WARNING, `pending_packages=[]`. The boolean signal is still valid; the package list is enrichment.
2. `PKGS_FILE` exists but unreadable → log a warning, treat as empty list. Don't promote to UNKNOWN.
3. `PKGS_FILE` contains blank lines or trailing whitespace → strip and filter empties before reporting.
4. Unexpected exception inside `check()` → caught by `BaseChecker.run()`, surfaced as UNKNOWN with the error message.
5. Path safety: both flag paths are hard-coded absolute string literals, no user input touches them. `Path.resolve()` not strictly required, but applied on read for consistency with the project's "always use absolute paths" rule.

## Alert lifecycle integration

The checker is fully stateless. The "pending since" / "out loud, doesn't quiet down" / "auto-resolve on reboot" properties are delivered by `CheckAlertBridge` (`apps/alerts/check_integration.py`) without any extra wiring:

| Event | What happens |
|-------|--------------|
| File first appears post-upgrade | Checker reports WARNING → `_process_alert` finds no matching fingerprint → `_create_alert` opens `Alert(status=FIRING, started_at=now)` and an `Incident`. |
| Subsequent runs, file still present | Checker reports WARNING → fingerprint `(reboot_debian, hostname)` matches existing FIRING alert → `_update_alert` refreshes `severity`, `description`, `annotations`, `raw_payload`. **`started_at` is not in the update_fields list, so it stays anchored to the original opening.** |
| Reboot completes, file gone | Checker reports OK → `STATUS_TO_ALERT_STATUS[OK] = "resolved"` → `_resolve_alert` sets `status=RESOLVED, ended_at=now`, writes `AlertHistory("resolved")` event → `_check_incident_resolution` auto-closes the parent Incident if no other firing alerts remain on it. |

Two consequences worth highlighting:

- **Alert quiets down only on actual reboot, not on follow-up upgrades.** Even when a follow-up `apt upgrade` adds new packages to `.pkgs` mid-pending, the fingerprint is identical, so `started_at` does not move and the alert stays open with stable age.
- **Pending package list stays current in annotations.** `_update_alert` rewrites annotations from each new CheckResult, so operators see the *current* pending-packages set on the same Alert.

## Testing

Tests live in `apps/checkers/_tests/checkers/test_reboot_debian.py`, mirroring the source path. Patch `sys` and the filesystem-touching helpers; assert on the returned `CheckResult`. No real filesystem reads in any test. Django `TestCase` for the integration test (in-memory DB).

### Test classes

**`RebootDebianCheckerPlatformTests`** — gating
- `test_skipped_on_macos` — `sys.platform = "darwin"` → OK, "not Linux"
- `test_skipped_on_windows` — `sys.platform = "win32"` → OK, "not Linux"
- `test_skipped_on_non_debian_linux` — Linux + `ID=fedora` → OK, "not Debian-family"
- `test_skipped_when_os_release_missing` — Linux + os-release absent → OK, "cannot determine distro"
- `test_skipped_when_os_release_unreadable` — `read_text` raises `OSError` → OK skip
- `test_accepts_debian_via_id` — `ID=debian` → continues past gating
- `test_accepts_ubuntu_via_id` — `ID=ubuntu` → continues past gating
- `test_accepts_derivative_via_id_like` — `ID=linuxmint`, `ID_LIKE="ubuntu debian"` → continues
- `test_os_release_handles_quoted_values` — values wrapped in `"..."` and `'...'` parse cleanly

**`RebootDebianCheckerStatusTests`** — happy + warning paths
- `test_ok_when_flag_absent` — Debian + flag missing → OK, `reboot_required=False`
- `test_warning_when_flag_present_with_packages` — flag + `.pkgs` with `linux-image-generic\nlibc6` → WARNING, `pending_packages=["linux-image-generic", "libc6"]`, count 2
- `test_warning_when_flag_present_no_pkgs_file` — flag + `.pkgs` missing → WARNING, empty packages, message "Reboot required"
- `test_warning_when_pkgs_file_unreadable` — flag + `.pkgs` raises `OSError` → WARNING, empty packages (not UNKNOWN)
- `test_pkgs_file_strips_blank_lines_and_whitespace` — `"linux-image\n\n  libc6  \n\n"` → `["linux-image", "libc6"]`

**`RebootDebianCheckerErrorTests`** — catch-all
- `test_unexpected_exception_returns_unknown` — patch `REBOOT_FLAG.exists` to raise `RuntimeError`, call `.run()` not `.check()` → status UNKNOWN

**`RebootDebianRegistryTests`** — wiring
- `test_registered_in_checker_registry` — `CHECKER_REGISTRY["reboot_debian"] is RebootDebianChecker`
- `test_exported_from_package` — `from apps.checkers.checkers import RebootDebianChecker` succeeds

**`RebootDebianAlertIntegrationTests`** — end-to-end (Django `TestCase`, real DB)
- `test_warning_then_resolved_keeps_started_at_stable` — three sequential `CheckAlertBridge.process_check_result` calls:
  1. flag present → assert Alert created with `status=FIRING`, capture `started_at`
  2. flag still present → assert same Alert, `started_at` unchanged, annotations updated
  3. flag absent → assert Alert `status=RESOLVED`, `ended_at` populated, parent Incident auto-resolved

This integration test is the highest-value case: it proves the "open on first observation, stable across the streak, auto-resolves on reboot" property end-to-end.

### Patch strategy

Prefer `unittest.mock.patch` scoped to the checker module:
- `apps.checkers.checkers.reboot_debian.sys`
- `apps.checkers.checkers.reboot_debian.Path.exists` / `.read_text` (or extract `_flag_present()` and `_read_pkgs()` helpers and patch those if mocking `Path` becomes clumsy — same pattern `disk_linux` uses with `scan_directory` / `find_old_files`).

### Coverage

Project CLAUDE.md requires 100% branch coverage per PR. The cases above cover every branch:
- platform gate (linux / non-linux)
- os-release path (missing / unreadable / parsable)
- distro detection (debian / ubuntu / `ID_LIKE` / non-debian)
- flag presence (yes / no)
- pkgs file (missing / empty / unreadable / with-content / with-blank-lines)
- the `BaseChecker.run()` catch-all UNKNOWN path

## Non-goals / explicitly rejected alternatives

- **CheckRun history reads inside `check()`** — initially considered to compute "pending since" age. Rejected: would make `reboot_debian` the only stateful checker in the codebase and the alerts layer already provides the same property via `Alert.started_at` consistently for every check.
- **File mtime as age signal** — initially proposed. Rejected: APT re-touches the file on every follow-up triggering upgrade, which would *reset* the age signal exactly when the situation is getting worse. The alert would quiet down as the problem grew.
- **MOTD parsing (`/var/lib/update-notifier/updates-available`)** — managed hosts (DigitalOcean, Hetzner, AWS) overwrite this file with promotional content, so we'd be reading marketing copy rather than state.
- **Pending-update / security-update counts** — would require shelling out to `apt list --upgradable`, slow and network-aware. Out of scope; could land later as a separate `apt_updates` checker if wanted.
- **`os_linux_debian` bundled checker** — initially considered to match the `disk_linux` "OS-family namespace, multiple signals" shape. Rejected: the bundle dissolves once unreliable signals (MOTD, apt counts) are dropped — only one signal remains.