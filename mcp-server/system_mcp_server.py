"""
MCP server exposing SBOM and system owner data.
"""

import logging
from json import JSONDecodeError
import click
from starlette.requests import Request
from starlette.responses import JSONResponse
from common import build_fastmcp_server, load_json_fixture
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root

logger = configure_component_logger(
    logger_name=__name__,
    component_group="mcp",
    component_name="system_mcp_server",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="mcp-system-console",
    file_tag="mcp-system-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)

SBOM_FIXTURE = "sbom.json"
SYSTEM_OWNER_FIXTURE = "system_owners.json"

mcp = build_fastmcp_server("System Data Server", port=PipelineConfig.SYSTEM_MCP_PORT)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    """
    Readiness/liveness probe for the system MCP server.
    Args:
        _ (Request): Incoming HTTP request (unused).
    Returns:
        JSONResponse: Status payload with server metadata.
    """
    payload = {
        "status": "ok",
        "service": "system_mcp_server",
        "port": PipelineConfig.SYSTEM_MCP_PORT,
    }
    return JSONResponse(payload)


@mcp.tool()
def get_sbom_data() -> str:
    """
    Fetch SBOM details for internal systems (no arguments required).
    """
    try:
        return load_json_fixture(SBOM_FIXTURE)
    except FileNotFoundError as exc:
        logger.error("SBOM fixture missing: %s", exc, exc_info=True)
        return f"Error reading {SBOM_FIXTURE}: {exc}"
    except JSONDecodeError as exc:
        logger.error("SBOM fixture invalid JSON: %s", exc, exc_info=True)
        return f"Error reading {SBOM_FIXTURE}: {exc}"
    except OSError as exc:
        logger.error("SBOM fixture unreadable: %s", exc, exc_info=True)
        return f"Error reading {SBOM_FIXTURE}: {exc}"

@mcp.tool()
def get_system_owner_data() -> str:
    """
    Fetch system owner details for internal systems (no arguments required).
    """
    try:
        return load_json_fixture(SYSTEM_OWNER_FIXTURE)
    except FileNotFoundError as exc:
        logger.error("System owner fixture missing: %s", exc, exc_info=True)
        return f"Error reading {SYSTEM_OWNER_FIXTURE}: {exc}"
    except JSONDecodeError as exc:
        logger.error("System owner fixture invalid JSON: %s", exc, exc_info=True)
        return f"Error reading {SYSTEM_OWNER_FIXTURE}: {exc}"
    except OSError as exc:
        logger.error("System owner fixture unreadable: %s", exc, exc_info=True)
        return f"Error reading {SYSTEM_OWNER_FIXTURE}: {exc}"


@click.command()
@click.option("--port", default=PipelineConfig.SYSTEM_MCP_PORT, help="Port to run the MCP server on")
def main(port: int):
    # Update port if overridden
    mcp.port = port
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()
