"""
Pydantic schemas for the gateway HTTP API.
"""

from typing import Any, Dict, Optional, Literal
from pydantic import BaseModel


class CapabilityExecutionRequest(BaseModel):
    """
    Execution request payload for capability invocation.
    """
    capability_id: str
    arguments: Dict[str, Any]


class CapabilityExecutionResponse(BaseModel):
    """
    Standardized response after executing a capability.
    """
    capability_id: str
    result: Any

class CapabilitySummary(BaseModel):
    """Unified summary for a tool or agent capability."""

    capability_id: str
    summary: str

DataType = Literal["text", "object"]

class CapabilityDetail(BaseModel):
    """Unified detail payload for a tool or agent capability."""

    capability_id: str
    description: str # Full description
    input_type: DataType
    input_schema: Optional[Dict[str, Any]] | None
    output_type: DataType
    output_schema: Optional[Dict[str, Any]] | None
