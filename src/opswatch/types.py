from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    target: str
    key: str
    kind: str
    healthy: bool | None
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: str = "warning"
    immediate: bool = False
    action: str = "No action taken"
    next_step: str = "Inspect the evidence with /diagnose"
    dedup_key: str | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessEvent:
    target: str
    event_id: str
    timestamp: float
    client_ip: str
    path: str
    status: int
    trusted: bool = False
