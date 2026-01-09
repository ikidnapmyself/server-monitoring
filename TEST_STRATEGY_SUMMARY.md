# Test Strategy Summary

## Overview

The alerts app test suite has been reorganized from a **monolithic 746-line file** into **focused, modular test files** organized by functional area. This provides:

- ✅ **Clarity**: Each file has a single responsibility
- ✅ **Maintainability**: Easy to find and update tests
- ✅ **Parallelization**: Faster test execution with pytest
- ✅ **Reusability**: Shared fixtures in `conftest.py`
- ✅ **Documentation**: STRATEGY.md + README_TESTING.md

---

## Files & Coverage

### Test Files (8 modules)

| File | Lines | Tests | Purpose |
|------|-------|-------|---------|
| `conftest.py` | ~150 | 10 fixtures | Shared fixtures for all tests |
| `test_drivers_built_in.py` | ~280 | ~25 | AlertManager, Grafana, Generic drivers |
| `test_drivers_external.py` | ~320 | ~35 | PagerDuty, NewRelic, Datadog, Zabbix |
| `test_driver_detection.py` | ~160 | ~15 | Auto-detection & driver lookup |
| `test_orchestrator.py` | ~350 | ~35 | AlertOrchestrator service |
| `test_incident_manager.py` | ~280 | ~20 | IncidentManager service |
| `test_models.py` | ~300 | ~25 | Alert & Incident models |
| `test_views.py` | ~240 | ~20 | HTTP webhook endpoints |
| `test_integration.py` | ~280 | ~15 | End-to-end workflows |
| **Total** | **~2,360** | **~190** | |

### Documentation Files

| File | Purpose |
|------|---------|
| `STRATEGY.md` | Comprehensive test strategy & philosophy |
| `README_TESTING.md` | Practical testing guide & examples |
| `__init__.py` | Package marker + imports for backward compatibility |

---

## Module Breakdown

### 1. `conftest.py` — Shared Fixtures

**Provides:**
- `alertmanager_payload`, `alertmanager_resolved_payload`
- `grafana_payload`, `generic_payload`
- `pagerduty_v3_payload`, `newrelic_classic_payload`, `newrelic_workflow_payload`
- `datadog_payload`, `zabbix_payload`
- `orchestrator`, `orchestrator_no_auto_create`, `orchestrator_no_auto_resolve`
- `client` (Django test client)

**Usage:**
```python
def test_something(self, alertmanager_payload, orchestrator):
    result = orchestrator.process_webhook(alertmanager_payload)
    assert result.alerts_created == 1
```

---

### 2. `test_drivers_built_in.py` — AlertManager, Grafana, Generic

**Test Classes:**
- `TestAlertManagerDriver` (11 tests)
- `TestGrafanaDriver` (6 tests)
- `TestGenericWebhookDriver` (8 tests)

**Coverage:**
- ✅ Validation of payload structure
- ✅ Parsing fields (name, severity, status, fingerprint, labels)
- ✅ Severity mapping
- ✅ Status transitions (firing ↔ resolved)
- ✅ Missing field handling
- ✅ Flexible field name support (generic driver)

---

### 3. `test_drivers_external.py` — PagerDuty, NewRelic, Datadog, Zabbix

**Test Classes:**
- `TestPagerDutyDriver` (5 tests)
- `TestNewRelicDriver` (7 tests)
- `TestDatadogDriver` (8 tests)
- `TestZabbixDriver` (8 tests)

**Coverage:**
- ✅ V2/V3 format support (PagerDuty)
- ✅ Classic/workflow format (NewRelic)
- ✅ Tag parsing (string + list formats)
- ✅ Numeric severity mapping (Zabbix 0-5)
- ✅ Event type parsing (resolved, acknowledged)
- ✅ Priority/urgency mapping
- ✅ Fingerprint generation

---

### 4. `test_driver_detection.py` — Factory & Auto-Detection

**Test Classes:**
- `TestDriverDetection` (9 tests)
- `TestDriverFactory` (11 tests)

**Coverage:**
- ✅ Auto-detection by payload structure
- ✅ Fallback to generic driver
- ✅ Named driver lookup (`get_driver("alertmanager")`)
- ✅ Invalid driver name error handling
- ✅ Case-insensitive driver names
- ✅ Detection order validation

---

### 5. `test_orchestrator.py` — AlertOrchestrator Service

**Test Classes:**
- `TestOrchestratorAlertCreation` (4 tests)
- `TestOrchestratorAlertResolution` (2 tests)
- `TestOrchestratorIncidentCreation` (5 tests)
- `TestOrchestratorIncidentResolution` (4 tests)
- `TestOrchestratorErrorHandling` (4 tests)

**Coverage:**
- ✅ Alert creation from webhook
- ✅ Alert deduplication by (fingerprint, source)
- ✅ Alert status updates
- ✅ AlertHistory events (created/resolved/refired)
- ✅ Incident auto-creation
- ✅ Incident attachment by alert name
- ✅ Incident severity escalation
- ✅ Incident auto-resolution
- ✅ Error handling (invalid driver, no detector, parse exceptions)

---

### 6. `test_incident_manager.py` — IncidentManager Service

**Test Classes:**
- `TestIncidentManagerAcknowledge` (3 tests)
- `TestIncidentManagerResolve` (4 tests)
- `TestIncidentManagerClose` (2 tests)
- `TestIncidentManagerNotes` (4 tests)
- `TestIncidentManagerQueries` (3 tests)

**Coverage:**
- ✅ Acknowledge (status + timestamp + metadata)
- ✅ Resolve (status + summary + timestamp + metadata)
- ✅ Close (status + timestamp)
- ✅ Add notes (accumulate + author + timestamp)
- ✅ Query optimizations (prefetch_related)

---

### 7. `test_models.py` — Data Models

**Test Classes:**
- `TestAlertModel` (9 tests)
- `TestIncidentModel` (11 tests)

**Coverage:**
- ✅ Alert properties (is_firing, duration)
- ✅ Incident properties (is_open, is_resolved, alert_count, firing_alert_count)
- ✅ Model methods (acknowledge, resolve, close)
- ✅ Deduplication (fingerprint + source)
- ✅ Uniqueness constraints
- ✅ Field validation
- ✅ Ordering & indexes
- ✅ Metadata storage (JSON)

---

### 8. `test_views.py` — HTTP Endpoints

**Test Classes:**
- `TestWebhookPostEndpoint` (8 tests)
- `TestWebhookGetEndpoint` (5 tests)
- `TestWebhookWithDifferentDrivers` (6 tests)

**Coverage:**
- ✅ POST /alerts/webhook/ (success, 200, status codes)
- ✅ GET /alerts/webhook/ (health check, 200, ok status)
- ✅ Invalid JSON (400 error)
- ✅ Forced driver in URL parameter
- ✅ Response structure (count fields, errors)
- ✅ Partial status (when orchestrator has errors)
- ✅ All driver payloads (AlertManager, Grafana, PagerDuty, Datadog, Zabbix)

---

### 9. `test_integration.py` — End-to-End Workflows

**Test Classes:**
- `TestAlertToIncidentWorkflow` (3 tests)
- `TestIncidentStateTransitions` (2 tests)
- `TestMultiSourceAlerts` (1 test)
- `TestErrorRecoveryWorkflow` (2 tests)

**Coverage:**
- ✅ Fire → resolve → auto-resolve incident
- ✅ Multi-alert incident with severity escalation
- ✅ AlertHistory event tracking
- ✅ Incident state machine (OPEN → ACK → RESOLVED → CLOSED)
- ✅ Multi-source alerts (AlertManager + Grafana in same incident)
- ✅ Idempotent processing (duplicate payloads)
- ✅ Fire → resolve → refire workflow

---

## Running Tests

### All Tests
```bash
pytest apps/alerts/tests/
```

### By Module
```bash
pytest apps/alerts/tests/test_drivers_built_in.py
pytest apps/alerts/tests/test_orchestrator.py
```

### By Class
```bash
pytest apps/alerts/tests/test_orchestrator.py::TestOrchestratorAlertCreation
```

### By Test
```bash
pytest apps/alerts/tests/test_orchestrator.py::TestOrchestratorAlertCreation::test_process_creates_alert_from_payload
```

### With Coverage
```bash
pytest --cov=apps.alerts apps/alerts/tests/
```

### Verbose
```bash
pytest -v apps/alerts/tests/
```

---

## Documentation

### For Strategy & Design
**→ Read `STRATEGY.md`**
- Testing philosophy
- Coverage areas (8 sections)
- Testing patterns & fixtures
- Coverage goals
- Continuous improvement

### For Practical Usage
**→ Read `README_TESTING.md`**
- Quick start commands
- Test structure & file organization
- Writing new tests (with examples)
- Debugging failed tests
- Best practices & anti-patterns
- CI/CD integration examples

---

## Key Improvements

### Before
- **746 lines** in single `tests.py` file
- Hard to locate specific test
- Long file loads slowly
- Difficult to run subset of tests
- No fixtures (duplicated test data)
- No documentation of test strategy

### After
- **~2,360 lines** across 8 focused modules
- Clear naming: `test_drivers_built_in.py`, `test_orchestrator.py`, etc.
- Fast file loading, easy to navigate
- Run tests by module/class/individual test
- **Shared fixtures** in `conftest.py` (DRY principle)
- **Comprehensive documentation** (STRATEGY.md + README_TESTING.md)

### Benefits
✅ **Maintainability** — Easy to find, update, and extend tests  
✅ **Clarity** — Each module has a single responsibility  
✅ **Performance** — Faster test discovery & parallel execution  
✅ **Reusability** — Fixtures avoid test data duplication  
✅ **Documentation** — Clear testing strategy for team alignment  
✅ **Scalability** — Easy to add new tests as app grows  

---

## Next Steps

### For Developers
1. Read [README_TESTING.md](README_TESTING.md) for practical guidance
2. Use fixtures from `conftest.py` when adding tests
3. Follow test naming convention: `test_<subject>_<condition>_<result>`
4. Run tests frequently: `pytest apps/alerts/tests/`

### For CI/CD
1. Integrate `pytest apps/alerts/tests/` into pipeline
2. Enforce coverage: `--cov=apps.alerts` (target 90%+)
3. Run on every commit/PR

### For Future Improvements
1. Add property-based testing (hypothesis)
2. Add performance/load tests
3. Add mutation testing to validate test quality
4. Document expected behavior in test docstrings

---

## Backward Compatibility

The old `tests.py` has been backed up as `tests.py.bak` and can be deleted.

All test classes are re-exported in `__init__.py` for backward compatibility:
```python
# This still works
from apps.alerts.tests import TestAlertManagerDriver

# But also do this (preferred)
from apps.alerts.tests.test_drivers_built_in import TestAlertManagerDriver
```


