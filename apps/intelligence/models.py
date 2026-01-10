"""
Intelligence models.

Note: Analysis execution tracking is handled by the orchestration layer
(PipelineRun + StageExecution). This app focuses on provider configuration
and recommendation data structures.

The intelligence stage output (summary, recommendations, token usage, etc.)
is stored in StageExecution.output_snapshot by the orchestrator.
"""

# No Django models needed - orchestration handles all tracking.
# Provider configuration can be added here if persistent config is needed.
#
# Data structures for analysis output are defined in:
# - apps/intelligence/providers/base.py (Recommendation, RecommendationType, etc.)
