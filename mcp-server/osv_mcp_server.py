"""
MCP server exposing OSV vulnerability data.
"""

import logging
import click
import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from common.server import build_fastmcp_server
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root

logger = configure_component_logger(
    logger_name=__name__,
    component_group="mcp",
    component_name="osv_mcp_server",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="mcp-osv-console",
    file_tag="mcp-osv-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)

OSV_API_BASE = "https://api.osv.dev/v1/vulns"

# Default port from config, can be overridden in main
mcp = build_fastmcp_server("OSV Data Server", port=PipelineConfig.OSV_MCP_PORT)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    """
    Readiness/liveness probe for the OSV MCP server.
    Args:
        _ (Request): Incoming HTTP request (unused).
    Returns:
        JSONResponse: Status payload with server metadata.
    """
    payload = {
        "status": "ok",
        "service": "osv_mcp_server",
        "port": PipelineConfig.OSV_MCP_PORT,
    }
    return JSONResponse(payload)


@mcp.tool()
def get_osv_vulnerability_data(vulnerability_id: str) -> str:
    """
    Fetch OSV vulnerability details from the OSV API.

    Args:
        vulnerability_id: Vulnerability identifier in GHSA-xxxx-xxxx-xxxx format (case-insensitive prefix).
    Returns:
        str: JSON string with vulnerability details or an error message.
    """
    vid = (vulnerability_id or "").strip()
    if not vid:
        error_msg = "Invalid vulnerability ID: empty."
        logger.error(error_msg)
        return error_msg

    # Validate prefix (case-insensitive: accepts GHSA- or ghsa-)
    if not vid.upper().startswith("GHSA-"):
        error_msg = (
            f"Invalid vulnerability ID: {vid}. "
            "Must start with 'GHSA-' or 'ghsa-'."
        )
        logger.error(error_msg)
        return error_msg

    # Transform to canonical format: always GHSA- (uppercase) + rest (lowercase)
    rest = vid[5:].lower()
    canonical_id = "GHSA-" + rest

    url = f"{OSV_API_BASE}/{canonical_id}"
    logger.info(f"Fetching vulnerability data for {vid}; canonical form={canonical_id}")

    try:
        response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
        logger.info(f"OSV lookup success: requested={vid} resolved={canonical_id}")
        return response.text
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        error_msg = f"HTTP error fetching {canonical_id}: {status} - {exc.response.text}"
        logger.error(error_msg, exc_info=True)
        return error_msg
    except httpx.RequestError as exc:
        error_msg = f"Network error fetching {canonical_id}: {exc}"
        logger.error(error_msg, exc_info=True)
        return error_msg
    except Exception as exc:
        error_msg = f"Unexpected error fetching {canonical_id}: {exc}"
        logger.error(error_msg, exc_info=True)
        return error_msg


@click.command()
@click.option("--port", default=PipelineConfig.OSV_MCP_PORT, help="Port to run the MCP server on")
def main(port: int):
    # Update port if overridden
    mcp.port = port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
