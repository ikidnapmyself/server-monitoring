# Orchestration App Test Restructure

## Goal

Merge `apps/orchestration/tests.py` (22 tests, 6 classes) into the existing `_tests/` package per `agents.md`.

## Mapping

| Source class | Target file |
|---|---|
| `DTOSerializationTests` | `_tests/test_dtos.py` (new) |
| `PipelineRunModelTests` + `StageExecutionModelTests` | `_tests/test_models.py` (new) |
| `SignalTagsTests` | `_tests/test_signals.py` (new) |
| `OrchestratorTests` + `StageExecutionErrorTests` | `_tests/test_orchestrator.py` (new) |

## Notes

- Zero overlap with existing `_tests/` files
- No test logic changes — all 79 tests (57 existing + 22 moved) pass
- `tests.py` deleted after migration