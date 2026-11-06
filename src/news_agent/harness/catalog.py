"""Trusted registrations owned by domain executors sharing the harness config file.

These IDs are valid configuration references, not executable aliases. The owning executor
must apply its domain prerequisites (for example paid-decision leases) before execution.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainCapability:
    id: str
    owner: str


ALERT_ASSESS_CAPABILITY = DomainCapability(id="alerts.assess", owner="market_alerts")
DOMAIN_CAPABILITIES = (ALERT_ASSESS_CAPABILITY,)
