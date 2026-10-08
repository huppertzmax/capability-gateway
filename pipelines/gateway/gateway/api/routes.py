"""
FastAPI routes for capability discovery and execution.
"""

import time
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from gateway.gateway.config.config import CALLER_AGENT_ID_HEADER
from shared.logging_utils import configure_component_logger
from shared.model_loader import set_active_run_id
from shared.run_context import RUN_ID_HEADER
from gateway.gateway.services.capability_catalog import (
    CapabilityCatalog,
    CapabilityInputError,
    CapabilityNotAllowed,
    CapabilityNotFound,
)
from gateway.gateway.api.schemas import CapabilityDetail, CapabilitySummary, CapabilityExecutionRequest, CapabilityExecutionResponse

router = APIRouter()
logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-routes-console",
    file_tag="gateway-routes-file",
    file_mode="a",
)


def _get_capability_catalog(request: Request) -> CapabilityCatalog:
    """
    Retrieve the initialized CapabilityCatalog from the application state.
    Args:
        request: Incoming FastAPI request instance.
    Returns:
        CapabilityCatalog: Initialized service ready to be used.
    Raises:
        HTTPException: When the service is not available yet.
    """
    service = getattr(request.app.state, "capability_catalog", None)
    if not service:
        raise HTTPException(
            status_code=503,
            detail="Capability catalog still initializing",
        )
    return service

def _get_caller_agent_id(
    request: Request
) -> str:
    """
    Resolve and validate the caller identity from request headers.
    Args:
        request: Incoming FastAPI request instance.
    Returns:
        str: Caller agent id.
    Raises:
        HTTPException: When the caller header is missing
    """
    raw_caller_agent_id = request.headers.get(CALLER_AGENT_ID_HEADER)
    if raw_caller_agent_id is None or not raw_caller_agent_id.strip():
        raise HTTPException(
            status_code=401,
            detail=f"Missing {CALLER_AGENT_ID_HEADER} header",
        )
    return raw_caller_agent_id.strip()

def _get_run_id(
    request: Request
) -> str:
    """
    Resolve and validate the run identifier from request headers.
    Args:
        request: Incoming FastAPI request instance.
    Returns:
        str: run id.
    Raises:
        HTTPException: When the run id header is missing
    """
    raw_run_id = request.headers.get(RUN_ID_HEADER)
    if raw_run_id is None or not raw_run_id.strip():
        raise HTTPException(
            status_code=401,
            detail=f"Missing {RUN_ID_HEADER} header",
        )
    return raw_run_id.strip()

@router.get("/capabilities", response_model=List[CapabilitySummary])
async def get_capabilities(
    caller_agent_id: str = Depends(_get_caller_agent_id),
    run_id: str = Depends(_get_run_id),
    capability_catalog: CapabilityCatalog = Depends(_get_capability_catalog),
):
    """
    List all curated capabilities with minimal summaries.
    Args:
        caller_agent_id: Authorized caller agent id.
        capability_catalog: Capability catalog used to merge tool capabilities and agent skills.
    Returns:
        List[CapabilityMin]: Minimal capability views.
    """
    set_active_run_id(run_id)
    try:
        return await capability_catalog.list_capabilities(caller_agent_id)
    finally:
        set_active_run_id(None)

@router.get("/relevant_capabilities", response_model=List[CapabilitySummary])
async def get_relevant_capabilities(
    textual_query: str,
    top_k: int = 3,
    caller_agent_id: str = Depends(_get_caller_agent_id),
    run_id: str = Depends(_get_run_id),
    capability_catalog: CapabilityCatalog = Depends(_get_capability_catalog),
):
    """
    Get all relevant capabilities with minimal summaries for a textual query.
    Args:
        textual_query: Textual query to find relevant capabilities.
        caller_agent_id: Authorized caller agent id.
        capability_catalog: Capability catalog used to merge tool capabilities and agent skills.
    Returns:
        List[CapabilityMin]: Minimal capability views.
    """
    set_active_run_id(run_id)
    try:
        return await capability_catalog.retrieve_relevant_capabilities(textual_query, caller_agent_id, top_k)
    finally:
        set_active_run_id(None)


@router.get("/capabilities/{capability_id}", response_model=CapabilityDetail)
async def get_capability_details(
    capability_id: str,
    caller_agent_id: str = Depends(_get_caller_agent_id),
    run_id: str = Depends(_get_run_id),
    capability_catalog: CapabilityCatalog = Depends(_get_capability_catalog),
):
    """
    Retrieve curated details for a single capability.
    Args:
        capability_id: Stable capability identifier.
        caller_agent_id: Authorized caller agent id.
        capability_catalog: Capability catalog used to resolve capability targets.
    Returns:
        CapabilityFull: Full capability details.
    """
    set_active_run_id(run_id)
    try:
        return await capability_catalog.get_capability_details(
            capability_id,
            caller_agent_id
        )
    finally:
        set_active_run_id(None)

@router.post("/capabilities/execute", response_model=CapabilityExecutionResponse)
async def execute_capability(
    payload: CapabilityExecutionRequest,
    caller_agent_id: str = Depends(_get_caller_agent_id),
    run_id: str = Depends(_get_run_id),
    capability_catalog: CapabilityCatalog = Depends(_get_capability_catalog),
    request: Request = None,
):
    """
    Execute a capability by resolving it to a tool or agent skill via capability_id.
    Args:
        payload: Payload describing which capability to call and with what input.
        caller_agent_id: Authorized caller agent id.
        run_id: Stable run identifier for tracing and logging.
        capability_catalog: Capability catalog used to resolve capability targets.
    Returns:
        CapabilityExecutionResponse: Standardized response payload.
    """
    set_active_run_id(run_id)
    start_time = time.monotonic()
    try:
        try:
            execution = await capability_catalog.execute_capability(
                payload.capability_id,
                payload.arguments,
                caller_agent_id,
                run_id=run_id,
            )
        except CapabilityNotFound as exc:
            logger.warning(
                "execute failed (not found) caller=%s capability_id=%s run_id=%s error=%s",
                caller_agent_id,
                payload.capability_id,
                run_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=404, detail=str(exc))
        except CapabilityNotAllowed as exc:
            logger.warning(
                "execute failed (forbidden) caller=%s capability_id=%s run_id=%s error=%s",
                caller_agent_id,
                payload.capability_id,
                run_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=403, detail=str(exc))
        except CapabilityInputError as exc:
            logger.warning(
                "execute failed (input) caller=%s capability_id=%s run_id=%s error=%s",
                caller_agent_id,
                payload.capability_id,
                run_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.error(
                "execute failed (unexpected) caller=%s capability_id=%s run_id=%s error=%s",
                caller_agent_id,
                payload.capability_id,
                run_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(status_code=502, detail=f"Capability execution failed: {exc}") from exc
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            f"execute success caller={caller_agent_id} capability_id={payload.capability_id} run_id={run_id} "
            f"target={execution.kind} duration_ms={duration_ms}"
        )
        return CapabilityExecutionResponse(
            capability_id=payload.capability_id,
            result=execution.result,
        )
    finally:
        set_active_run_id(None)

@router.get("/")
async def root(request: Request):
    """
    Basic health endpoint that reports initialization status.
    Args:
        request: Incoming request.
    Returns:
        dict: Health payload used by monitoring checks.
    """
    initialized = getattr(request.app.state, "capability_catalog", None) is not None
    status = "initialized" if initialized else "initializing"
    return {"message": "Capability Gateway Server is running", "status": status}
