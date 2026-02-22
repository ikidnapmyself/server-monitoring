"""Context node handler — runs real system health checkers."""

import logging
from typing import Any, Dict

from apps.orchestration.nodes.base import BaseNodeHandler, NodeContext, NodeResult, NodeType

logger = logging.getLogger(__name__)


class ContextNodeHandler(BaseNodeHandler):
    node_type = NodeType.CONTEXT
    name = "context"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        from apps.checkers.checkers import CHECKER_REGISTRY, get_enabled_checkers
        from apps.checkers.checkers.base import CheckStatus

        node_id = config.get("id", "context")
        result = NodeResult(node_id=node_id, node_type="context")

        # Determine which checkers to run
        checker_names = config.get("checker_names") or list(get_enabled_checkers().keys())

        # Validate checker names against registry
        valid_names = []
        for name in checker_names:
            if name in CHECKER_REGISTRY:
                valid_names.append(name)
            else:
                logger.warning("Unknown checker: %s, skipping", name)

        if not valid_names:
            result.errors.append("No valid checkers to run")
            return result

        checks_run = 0
        checks_passed = 0
        checks_failed = 0
        results = {}

        for name in valid_names:
            checker_cls = CHECKER_REGISTRY[name]
            try:
                checker = checker_cls()
                check_result = checker.check()
                checks_run += 1

                if check_result.status == CheckStatus.OK:
                    checks_passed += 1
                else:
                    checks_failed += 1

                results[name] = {
                    "status": check_result.status.value,
                    "message": check_result.message,
                    "metrics": check_result.metrics,
                }
            except Exception as e:
                logger.exception("Checker %s failed with exception", name)
                checks_run += 1
                checks_failed += 1
                results[name] = {
                    "status": "unknown",
                    "message": str(e),
                    "metrics": {},
                }

        result.output = {
            "checks_run": checks_run,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "results": results,
        }
        return result

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        # No required fields — empty config runs all enabled checkers
        return []
