"""Persist sanitized capability metadata through the existing runtime repositories."""

import asyncio

from news_agent.observability.runtime import RuntimeTraceService


class CapabilityTrace:
    def __init__(self, session_factory, settings, context, profile):
        self.service = RuntimeTraceService(session_factory, settings)
        self.context = context
        self.profile = profile
        self.run_id = None
        self.owned = False
        self.failed = False
        self.lock = asyncio.Lock()

    async def __call__(self, event):
        async with self.lock:
            if self.run_id is None:
                state = self.context.data.get("supervisor_state", {})
                existing = state.get("runtime_run_id")
                self.owned = existing is None
                self.run_id = await self.service.ensure_run(
                    workflow="harness",
                    trigger=self.profile,
                    run_id=existing,
                    metadata={
                        "harness_run_id": event["run_id"],
                        "config_revision": event["config_revision"],
                        "registry_revision": event["registry_revision"],
                    },
                )
            step_id = await self.service.start_step(
                run_id=self.run_id,
                workflow="harness",
                step_name=event["capability_id"],
                step_type="tool",
                metadata=event,
            )
            self.failed |= event["status"] != "ok"
            await self.service.finish_step(
                step_id,
                status="completed" if event["status"] == "ok" else "failed",
                metadata=event,
            )

    async def finish(self, *, cancelled=False):
        if self.owned and self.run_id is not None:
            await self.service.finish_run(
                self.run_id,
                status="cancelled"
                if cancelled
                else "completed_with_errors"
                if self.failed
                else "completed",
            )
