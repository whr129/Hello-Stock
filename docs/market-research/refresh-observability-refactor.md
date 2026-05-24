# Refresh Observability and Retry Refactor

## Goal

Scheduled and manual market-research refreshes are measurable, queryable by the runtime agent, reported to Telegram, and more resilient to transient provider failures.

This remains part of the existing scheduler/runtime architecture. Do not add a new subagent. The runtime agent is the query surface for refresh history, reports, traces, and failures.

## Target Behavior

Each refresh produces structured metrics for:

- due, attempted, succeeded, failed, and skipped sources
- fetched, accepted, rejected, and saved article counts
- attempted, succeeded, and failed ticker snapshot fetches
- retry attempts and final provider outcomes
- source/ticker failure details
- duplicate, accepted, rejected, and low-signal source diagnostics
- total run duration and per-step durations

The final compact report is stored in `runtime_runs.metadata["refresh_report"]`, and a short human summary is kept in `runtime_runs.summary`.

Detailed per-source and per-ticker attempt data lives in `runtime_steps.metadata`, so `/trace <run-id>` and `/step <run-id> source:<name>` stay useful.

## Runtime Querying

The existing runtime agent handles refresh report queries instead of a separate subagent.

Runtime queries support:

- "last refresh"
- "refresh report"
- `/refreshreport [run-id]`
- `/job <run-id>`
- `/trace <run-id>`
- `/step <run-id> <step-name>`

"Last refresh" includes both scheduled `market_research_refresh` runs and manual `manual_refresh` runs.

## Telegram Report Delivery

Send a compact report after every scheduled and manual refresh.

Target the latest active user chat by selecting the newest `conversation_events.role == "user"` record. If no user chat exists, delivery is skipped and `report_delivery_status = "skipped_no_chat"` is recorded in runtime metadata.

Normal refresh reports are not stored as runtime alerts. `/alerts` remains focused on failures and operational alerts.

Suggested report format:

```text
Refresh report: run <id>
- Status: completed / completed_with_errors / failed
- Duration: <seconds>s
- Sources: <succeeded>/<attempted> succeeded, <failed> failed
- Articles: <fetched> fetched, <saved> saved, <rejected> rejected
- Market snapshots: <count>
- Retries: <retry_count>
- Source health: healthy <n>, low_signal <n>, failing <n>
- Failures: <short first failure or none>
- Debug: /trace <id> or /job <id>
```

## Retry Design

Refresh retries are provider-level retries, not whole-job retries.

Retry individual source fetches and ticker snapshot fetches inside the scheduler fetch node. Do not retry normalization, embeddings, summaries, mention extraction, or scoring in this pass.

New settings:

- `SOURCE_FETCH_MAX_ATTEMPTS`, default `3`
- `SOURCE_FETCH_RETRY_BACKOFF_SECONDS`, default `2`
- `MARKET_FETCH_MAX_ATTEMPTS`, default `2`
- `MARKET_FETCH_RETRY_BACKOFF_SECONDS`, default `2`
- `REFRESH_REPORT_ENABLED`, default `true`

Use simple linear backoff:

```text
sleep_seconds = backoff_seconds * attempt_number
```

If all attempts fail, preserve current scheduler behavior: record the error, continue the refresh, mark the source fetch result failed, and complete the job as `completed_with_errors`.

## Implementation Notes

Add a `RefreshReportService` to format reports and deliver Telegram messages.

Add repository support to merge/update `RuntimeRun.run_metadata` after run creation.

Update scheduler fetch logic to collect attempt metadata, counts, elapsed timings, and final outcomes.

Update runtime formatting to show refresh report metadata when available.

## Test Plan

Cover:

- source fetch succeeds after retry and records attempt metadata
- source fetch fails after max attempts and refresh continues
- ticker fetch retry behavior mirrors source behavior
- refresh report metadata includes counts, failures, retries, and delivery status
- Telegram report sends to latest active user chat
- report delivery skips cleanly when no chat exists
- runtime agent finds `market_research_refresh` for "last refresh"
- runtime agent returns stored refresh report
- new settings load defaults and env overrides
