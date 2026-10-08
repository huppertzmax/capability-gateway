
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Literal

CapabilityKind = Literal["tool", "agent"]
DataType = Literal["text", "object"]

@dataclass(frozen=True)
class CapabilitySummary:
    """Unified summary for a tool or agent capability."""

    capability_id: str
    summary: str


@dataclass(frozen=True)
class CapabilityDetail:
    """Unified detail payload for a tool or agent capability."""

    capability_id: str
    description: str # Full description
    input_type: DataType
    input_schema: Optional[Dict[str, Any]] | None
    output_type: DataType
    output_schema: Optional[Dict[str, Any]] | None

@dataclass(frozen=True)
class Capability:
    """Unified representation of a tool or agent capability."""

    capability_id: str
    capability_name: str
    source_name: str # MCP tool name or agent name
    kind: CapabilityKind
    summary: str # Short description
    description: str # Full description
    input_type: DataType
    input_schema: Optional[Dict[str, Any]] | None
    output_type: DataType
    output_schema: Optional[Dict[str, Any]] | None

@dataclass(frozen=True)
class CapabilityExecutionResult:
    """Execution result with a resolved capability kind."""

    capability_id: str
    kind: CapabilityKind
    result: Any