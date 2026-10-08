"""
MCP server exposing mock tools with verbose descriptions for bloat testing.
"""
from __future__ import annotations
import json
import logging
import click
import random
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Callable, Dict, List, Mapping
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from common import build_fastmcp_server
from shared.config import PipelineConfig
from shared.model_loader import LANGFUSE_MANAGER
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root
from shared.run_context import require_run_id_from_context

logger = configure_component_logger(
    logger_name=__name__,
    component_group="mcp",
    component_name="bloat_mcp_server",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="mcp-bloat-console",
    file_tag="mcp-bloat-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)


CONFIG_DIR = Path(__file__).resolve().parent / "tool_descriptions"
DOCSTRING_DIR = CONFIG_DIR / "docstrings"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class ServerMetadata:
    """Runtime metadata for the bloat MCP server."""
    service: str
    port: int


SERVER_METADATA = ServerMetadata(
    service="bloat_mcp_server",
    port=PipelineConfig.BLOAT_MCP_PORT,
)


def _load_allow_tools_by_caller(path: Path) -> Dict[str, List[str]]:
    """Load bloat tools per caller from config."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    allow_tools_by_caller_raw = payload.get("allow_tools_by_caller")
    allow_tools_by_caller: Dict[str, List[str]] = {}
    for caller_id, tool_names_raw in allow_tools_by_caller_raw.items():
        tool_names = [tool_name.strip() for tool_name in tool_names_raw]
        allow_tools_by_caller[caller_id] = tool_names
    return allow_tools_by_caller


def _limit_allow_tools_by_steps(allow_tools_by_caller: Mapping[str, List[str]], tool_steps: int) -> Dict[str, List[str]]:
    """Limit tools by steps where each step enables one tool per caller."""
    if tool_steps < 0:
        return {caller_id: list(tool_names) for caller_id, tool_names in allow_tools_by_caller.items()}
    return {caller_id: random.choices(tool_names, k=tool_steps) for caller_id, tool_names in allow_tools_by_caller.items()}


def _flatten_allow_tools(allow_tools_by_caller: Mapping[str, List[str]]) -> List[str]:
    """Flatten caller -> tools mapping into one registration list."""
    allow_list: List[str] = []
    for tool_names in allow_tools_by_caller.values():
        allow_list.extend(tool_names)
    return allow_list


def _load_allow_list(path: Path, tool_steps: int) -> List[str]:
    """Load and limit bloat tools by the requested step count."""
    allow_tools_by_caller = _load_allow_tools_by_caller(path)
    limited_allow_tools = _limit_allow_tools_by_steps(allow_tools_by_caller, tool_steps)
    return _flatten_allow_tools(limited_allow_tools)


def _build_stub_response(tool_name: str, run_id: str) -> Dict[str, str]:
    """Build a stub response payload for the mock tools."""
    logger.info(f"Bloat MCP tool invoked: {tool_name} run_id={run_id}")
    LANGFUSE_MANAGER.log_event(
        run_id=run_id,
        name="BLOAT MCP",
        as_type="guardrail"
    )
    return {"status": "ok", "tool": tool_name, "message": "Mock capability executed"}


def _build_tool(tool_name: str, description: str) -> Callable[..., Dict[str, str]]:
    """Create a mock tool function with the provided docstring."""
    def tool(ctx: Context) -> Dict[str, str]:
        resolved_run_id = require_run_id_from_context(
            ctx,
            source="bloat_mcp_server request headers",
        )
        return _build_stub_response(tool_name, resolved_run_id)
    tool.__name__ = tool_name
    tool.__doc__ = description
    return tool


def _register_tools(mcp: FastMCP, tool_steps: int) -> None:
    """Register the provided tool names on the MCP server instance."""
    allow_list = _load_allow_list(CONFIG_PATH, tool_steps)
    logger.debug(f"Registering {len(allow_list)} bloat tools for tool_steps={tool_steps}.")
    for tool_name in allow_list:
        docstring_path = DOCSTRING_DIR / f"{tool_name}.txt"
        description = docstring_path.read_text(encoding="utf-8").strip()
        tool = _build_tool(tool_name, description)
        mcp.tool()(tool)


def health_check(_: Request) -> JSONResponse:
    payload = {
        "status": "ok",
        "service": SERVER_METADATA.service,
        "port": SERVER_METADATA.port,
    }
    return JSONResponse(payload)


@click.command()
@click.option(
    "--port",
    default=PipelineConfig.BLOAT_MCP_PORT,
    help="Port to run the MCP server on",
)
@click.option(
    "--tool-count",
    default=PipelineConfig.MCP_BLOAT_TOOL_COUNT,
    type=int,
    show_default=True,
    help="Number of bloat steps (<0 enables all tools). Each step adds one tool per caller.",
)
def main(port: int, tool_count: int) -> None:
    SERVER_METADATA.port = port
    mcp = build_fastmcp_server(name="Bloat MCP Server", port=port)
    _register_tools(mcp, tool_count)
    mcp.custom_route("/health", methods=["GET"])(health_check)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
