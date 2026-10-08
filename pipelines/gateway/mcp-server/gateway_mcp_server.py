"""
MCP server that proxies requests to the capability-first gateway HTTP API.
"""

import logging
from typing import Any, Dict, Optional
import click
from mcp.server.fastmcp import Context
from starlette.requests import Request
from starlette.responses import JSONResponse
from common import (
    AsyncGatewayHttpClient,
    GatewayRequestError,
    build_fastmcp_server,
)
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root
from shared.run_context import RUN_ID_HEADER, require_run_id_from_context

logger = configure_component_logger(
    logger_name=__name__,
    component_group="mcp",
    component_name="gateway_mcp_server",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="mcp-gateway-console",
    file_tag="mcp-gateway-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)

TIMEOUT_SECONDS = PipelineConfig.GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS
CALLER_HEADER = "X-Caller-Agent-Id"

mcp = build_fastmcp_server("Gateway Wrapper Server", port=PipelineConfig.GATEWAY_MCP_PORT)
gateway_client = AsyncGatewayHttpClient(
    base_url=PipelineConfig.GATEWAY_URL,
    timeout=TIMEOUT_SECONDS,
    default_headers=None,
)

@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    """
    Readiness/liveness probe for the gateway MCP server.
    Args:
        _ (Request): Incoming HTTP request (unused).
    Returns:
        JSONResponse: Status payload with server metadata.
    """
    payload = {
        "status": "ok",
        "service": "gateway_mcp_server",
        "port": PipelineConfig.GATEWAY_MCP_PORT,
    }
    return JSONResponse(payload)

def _require_caller_id(caller_id: str) -> str:
    if not caller_id or not caller_id.strip():
        raise ValueError("caller_id is required")
    return caller_id.strip()

async def get_all_capabilities(caller_id: str, ctx: Context) -> str:
    """
    Retrieve all curated capabilities from the gateway.
    """
    try:
        caller = _require_caller_id(caller_id)
        run = require_run_id_from_context(ctx, source="gateway_mcp_server request headers")
        logger.info(f"gateway_mcp_server: get_all_capabilities -> gateway caller={caller}")
        return await gateway_client.get("/capabilities", headers={CALLER_HEADER: caller, RUN_ID_HEADER: run})
    except ValueError as exc:
        logger.error(f"gateway_mcp_server: invalid input provided: {exc}", exc_info=True)
        raise
    except GatewayRequestError as exc:
        logger.error(f"gateway_mcp_server: get_all_capabilities failed: {exc}", exc_info=True)
        raise

async def get_relevant_capabilities(
    caller_id: str,
    textual_query: str,
    ctx: Context,
    top_k: int = 3,
) -> str:
    """
    Retrieve relevant curated capabilities from the gateway using a textual query.
    Only available in RAG mode.
    """
    try:
        caller = _require_caller_id(caller_id)
        run = require_run_id_from_context(ctx, source="gateway_mcp_server request headers")
        logger.info(f"gateway_mcp_server: get_relevant_capabilities caller={caller} query={textual_query}")

        return await gateway_client.get(
            "/relevant_capabilities",
            params={"textual_query": textual_query, "top_k": top_k},
            headers={CALLER_HEADER: caller, RUN_ID_HEADER: run},
        )
    except ValueError as exc:
        logger.error(f"gateway_mcp_server: invalid input provided: {exc}", exc_info=True)
        raise
    except GatewayRequestError as exc:
        logger.error(f"gateway_mcp_server: get_relevant_capabilities failed: {exc}", exc_info=True)
        raise


@mcp.tool()
async def get_capability_details(caller_id: str, capability_id: str, ctx: Context) -> str:
    """
    Retrieve curated metadata for a specific capability via the gateway.
    """
    try:
        caller = _require_caller_id(caller_id)
        run = require_run_id_from_context(ctx, source="gateway_mcp_server request headers")
        logger.info(f"gateway_mcp_server: get_capability_details caller={caller} id={capability_id}")
        return await gateway_client.get(
            f"/capabilities/{capability_id}",
            headers={CALLER_HEADER: caller, RUN_ID_HEADER: run},
        )
    except ValueError as exc:
        logger.error(f"gateway_mcp_server: invalid input provided: {exc}", exc_info=True)
        raise
    except GatewayRequestError as exc:
        logger.error(f"gateway_mcp_server: get_capability_details failed: {exc}", exc_info=True)
        raise

@mcp.tool()
async def execute_capability(
    caller_id: str,
    capability_id: str,
    ctx: Context,
    arguments: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Execute a curated capability through the gateway.
    Args:
        caller_id (str): Identifier of the agent invoking the capability.
        capability_id (str): Identifier of the capability to execute.
        arguments (Dict[str, Any] | None): Arguments required by the capability; wrap plain text in {'text': '<input>'}.
        ctx (Context): Request context used for run_id header resolution.
    Returns:
        str: Serialized JSON response from the gateway or an error payload.
    Note:
        The caller_id is provided by an interceptor; run_id is resolved from request headers.
    """
    try:
        caller_id = _require_caller_id(caller_id)
        run_id = require_run_id_from_context(ctx, source="gateway_mcp_server request headers")
        resolved_arguments = arguments if arguments is not None else {}
        logger.info(
            f"gateway_mcp_server: execute_capability caller={caller_id} id={capability_id} run_id={run_id} args={resolved_arguments}"
        )
        headers = {CALLER_HEADER: caller_id, RUN_ID_HEADER: run_id}
        return await gateway_client.post(
            "/capabilities/execute",
            {
                "capability_id": capability_id,
                "arguments": resolved_arguments,
            },
            headers=headers,
        )
    except GatewayRequestError as exc:
        logger.error(f"gateway_mcp_server: execute_capability failed id={capability_id} error={exc}", exc_info=True)
        raise
    except ValueError as exc:
        logger.error(f"gateway_mcp_server: execute_capability invalid input id={capability_id} error={exc}", exc_info=True)
        raise
    except Exception as exc:
        logger.error(
            f"gateway_mcp_server: unexpected error while executing capability id={capability_id} error={exc}",
            exc_info=True,
        )
        raise

@click.command()
@click.option("--port", default=PipelineConfig.GATEWAY_MCP_PORT, help="Port to run the MCP server on")
@click.option("--gateway-url", default=PipelineConfig.GATEWAY_URL, help="URL of the Gateway")
@click.option("--mode", default="standard", type=click.Choice(["standard", "RAG"]), help="Server mode")
def main(port: int, gateway_url: str, mode: str):
    # Update configuration if overridden
    mcp.port = port

    # Re-initialize client if URL changed (though base_url attribute might be settable,
    # re-init is safer if AsyncGatewayHttpClient does logic in init)
    global gateway_client
    gateway_client = AsyncGatewayHttpClient(
        base_url=gateway_url,
        timeout=TIMEOUT_SECONDS,
        default_headers=None,
    )

    # Conditionally register RAG-only tools
    if mode == "RAG":
        logger.info("Initializing semantic capability search due to RAG mode")
        mcp.tool()(get_relevant_capabilities)
    else:
        logger.info("Initializing get all capabilities tool due to standard mode")
        mcp.tool()(get_all_capabilities)

    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()
