# Runtime Observability

The runtime subsystem owns persistent operational state:

- `RunRecord`: one row per chat request, scheduler refresh, recap run, or manual operation
- `StepTrace`: ordered step history for supervisor nodes, subagent calls, tool/provider calls, and scheduler steps
- `ErrorRecord`: normalized runtime failures with step name, run id, workflow, and concise error details
- alert-delivery metadata for operator notifications

Each trace step should record:

- `run_id`
- `workflow` such as `chat`, `scheduler`, `manual_refresh`, or `daily_recap`
- `step_name`
- `step_type` such as `node`, `subagent`, `provider`, or `tool`
- `parent_step_id`
- `started_at`, `completed_at`, `duration_ms`
- `status`: `running`, `completed`, `failed`, or `skipped`
- compact structured metadata
- optional `error_message`

