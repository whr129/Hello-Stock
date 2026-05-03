# Runtime Query Surface

`runtime_agent` should expose explicit operational commands:

- `/runtime` for the latest run summary
- `/job <run-id>` for a specific run
- `/trace <run-id>` for the ordered step sequence
- `/step <run-id> <step-name>` for one refresh step or node/provider call
- `/alerts` for recent failures and alertable events

Equivalent natural-language queries should also route to `runtime_agent` when they are clearly about execution history, refresh behavior, or recent runtime errors. Trace output includes run metadata, step metadata, parent/child nesting when recorded, and any runtime errors for the run.

