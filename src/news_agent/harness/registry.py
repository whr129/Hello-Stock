from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from news_agent.harness.contracts import CapabilitySpec


def tool_alias(capability_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", capability_id):
        return capability_id
    name = re.sub(r"[^A-Za-z0-9_-]", "_", capability_id)[:48]
    return f"{name}_{hashlib.sha256(capability_id.encode()).hexdigest()[:12]}"


@dataclass(frozen=True)
class RegistrySnapshot:
    specs: Mapping[str, CapabilitySpec]
    aliases: Mapping[str, str]
    revision: str

    def resolve(self, name: str) -> CapabilitySpec | None:
        return self.specs.get(self.aliases.get(name, name))

    def catalog(self, allowed_ids: set[str] | frozenset[str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool_alias(spec.id),
                    "description": spec.description,
                    "parameters": spec.input_schema or spec.input_model.model_json_schema(),
                },
            }
            for spec in self.specs.values()
            if spec.id in allowed_ids
        ]


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self.register_many([spec])

    def register_many(self, specs: list[CapabilitySpec]) -> None:
        combined = dict(self._specs)
        for spec in specs:
            if spec.id in combined:
                raise ValueError(f"Duplicate capability: {spec.id}")
            combined[spec.id] = spec
        aliases = {tool_alias(name): name for name in combined}
        if len(aliases) != len(combined) or any(
            alias in combined and alias != name for alias, name in aliases.items()
        ):
            raise ValueError("Capability aliases collide")
        self._specs = combined

    def snapshot(self) -> RegistrySnapshot:
        specs = dict(self._specs)
        descriptions = [
            {
                "id": spec.id,
                "kind": spec.kind,
                "effect": spec.effect,
                "input": spec.input_schema or spec.input_model.model_json_schema(),
                "output": spec.output_model.model_json_schema() if spec.output_model else None,
                "children": sorted(spec.allowed_children),
                "timeout": spec.timeout_seconds,
                "attempts": spec.max_attempts,
                "required": spec.required_capability,
            }
            for spec in specs.values()
        ]
        revision = hashlib.sha256(json.dumps(descriptions, sort_keys=True).encode()).hexdigest()
        return RegistrySnapshot(
            MappingProxyType(specs),
            MappingProxyType({tool_alias(name): name for name in specs}),
            revision,
        )

    def resolve(self, name: str) -> CapabilitySpec | None:
        return self.snapshot().resolve(name)

    def catalog(self, allowed_ids: set[str] | frozenset[str]) -> list[dict[str, Any]]:
        return self.snapshot().catalog(allowed_ids)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._specs)
