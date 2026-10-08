"""
Shared run-context constants and helpers for cross-component run-id propagation.
"""
from typing import Any, Optional

RUN_ID_HEADER = "X-Run-Id"


def clean_run_id(run_id: Optional[str]) -> Optional[str]:
    """
    Normalize a run identifier by stripping whitespace and collapsing empties.
    Args:
        run_id: Candidate run identifier.
    Returns:
        Optional[str]: Normalized run identifier or None.
    """
    if run_id is None:
        return None
    normalized = run_id.strip()
    return normalized or None


def require_run_id(run_id: Optional[str], *, source: str) -> str:
    """
    Validate that a run identifier is present and non-empty.
    Args:
        run_id: Candidate run identifier.
        source: Human-readable source label used in the error message.
    Returns:
        str: Normalized run identifier.
    Raises:
        ValueError: If the run identifier is missing.
    """
    normalized = clean_run_id(run_id)
    if normalized is None:
        raise ValueError(f"Missing run_id from {source}.")
    return normalized


def require_run_id_from_context(ctx: Any, *, source: str) -> str:
    """
    Resolve a run identifier from a FastMCP request context.
    Args:
        ctx: FastMCP context containing the current HTTP request.
        source: Human-readable source label used in the error message.
    Returns:
        str: Normalized run identifier.
    Raises:
        ValueError: If the context or run identifier is missing.
    """
    try:
        request = ctx.request_context.request
        return require_run_id(request.headers.get(RUN_ID_HEADER), source=source)
    except AttributeError as exc:
        raise ValueError(f"Missing run_id from {source}.") from exc
