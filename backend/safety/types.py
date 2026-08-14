"""Types used by the deterministic safety engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class SafetyRule:
    """An auditable emergency warning-sign rule."""

    id: str
    category: str
    description: str
    aliases: tuple[str, ...]
    enabled: bool


class TriggeredSafetyRule(TypedDict):
    """JSON-ready representation of a matched safety rule."""

    id: str
    category: str
    description: str


class SafetyDecision(TypedDict):
    """JSON-ready deterministic safety decision."""

    isEmergency: bool
    ruleSetVersion: str
    triggeredRules: list[TriggeredSafetyRule]
