"""Admin configuration for intelligence app.

Note: Intelligence analysis execution is tracked by the orchestration layer
(PipelineRun + StageExecution), not by separate models in this app.

To view intelligence stage executions:
- Go to /admin/orchestration/stageexecution/
- Filter by stage="analyze"

The StageExecution.output_snapshot contains the analysis results:
- summary, probable_cause, recommendations, confidence
- token usage (prompt_tokens, completion_tokens, total_tokens)
- model_info, provider details
"""

# No models to register - orchestration handles all tracking.
# Provider configuration admin can be added here if persistent config models are created.
