"""
MCP server exposing a mock notification tool that writes outbound messages to disk.
"""

from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import click
from mcp.server.fastmcp import Context
from starlette.requests import Request
from starlette.responses import JSONResponse
from common import build_fastmcp_server
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger, mirror_handlers_to_root
from shared.run_context import require_run_id_from_context

logger = configure_component_logger(
    logger_name=__name__,
    component_group="mcp",
    component_name="notification_mcp_server",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="mcp-notification-console",
    file_tag="mcp-notification-file",
    file_mode="a",
)
mirror_handlers_to_root(
    logger=logger,
    level=logging.WARNING,
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # mcp-server/ -> project_root/
OUTBOX_DIR = PROJECT_ROOT / "evaluation" / "data" / "tmp" / "notifications"

mcp = build_fastmcp_server("Notification Server", port=PipelineConfig.NOTIFICATION_MCP_PORT)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    """
    Readiness/liveness probe for the notification MCP server.
    Args:
        _ (Request): Incoming HTTP request (unused).
    Returns:
        JSONResponse: Status payload with server metadata.
    """
    payload = {
        "status": "ok",
        "service": "notification_mcp_server",
        "port": PipelineConfig.NOTIFICATION_MCP_PORT,
    }
    return JSONResponse(payload)


def _normalize_ghsa(ghsa_id: str) -> str:
    """Normalize GHSA ID for consistent comparison."""
    return (ghsa_id or "").strip().upper()


def _load_existing_notifications(path: Path) -> List[Dict[str, Any]]:
    """Load existing notifications from disk; tolerate malformed content."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [raw]
    except FileNotFoundError:
        return []


def _write_outbox_file(payload: Dict[str, Any]) -> str:
    """Append a notification payload to the run file and return the path."""
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    run_id = payload["run_id"]
    path = OUTBOX_DIR / f"{run_id}.json"
    existing = _load_existing_notifications(path)
    existing.append(payload)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


@mcp.tool()
def send_security_email_notification(
    owner_email: str,
    owner_name: str,
    affected_systems: List[str],
    package_name: str,
    ghsa_id: str,
    package_version: Optional[str],
    vulnerable_range_start: Optional[str],
    vulnerable_range_end: Optional[str],
    ctx: Context,
) -> Dict[str, Any]:
    """Persist a structured security notification to the local outbox.

    IMPORTANT (evaluation ground-truth format):
    - `package_name` must be the Maven coordinate `groupId:artifactId` (example: `com.alibaba:fastjson`),
      not a purl like `pkg:maven/com.alibaba/fastjson`.
    - If a value is unknown, pass JSON `null` (Python `None`) — do NOT use placeholders like `0` or `"null"`.
      This applies especially to `package_version` and vulnerable range bounds.
    - If OSV uses `"introduced": "0"` (meaning "no lower bound"), set `vulnerable_range_start` to `null`.
    - Range semantics: `vulnerable_range_end` should be the first fixed version (exclusive end),
      not the "last known affected" version.
    - Even if values are unknown, include keys with JSON null (these keys are treated as required by evaluation):
      `package_version`, `vulnerable_range_start`, `vulnerable_range_end`.

    Args:
        owner_email: Email address of the system owner
        owner_name: Name of the system owner
        affected_systems: List of affected system identifiers
        package_name: Vulnerable package name in fixed format `groupId:artifactId` (Maven)
        package_version: SBOM package version, or null if missing/unknown
        ghsa_id: GitHub Security Advisory ID (GHSA-....)
        vulnerable_range_start: Start of vulnerable version range, or null if unknown/open
        vulnerable_range_end: End of vulnerable version range (first fixed version!!!, exclusive), or null if unknown
        ctx: FastMCP request context (used to read run id from request headers).
    """
    resolved_run_id = require_run_id_from_context(
        ctx,
        source="notification_mcp_server request headers",
    )
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
    payload: Dict[str, Any] = {
        "owner_email": owner_email,
        "owner_name": owner_name,
        "affected_systems": affected_systems,
        "package_name": package_name,
        "package_version": package_version,
        "ghsa_id": _normalize_ghsa(ghsa_id),
        "vulnerable_range_start": vulnerable_range_start,
        "vulnerable_range_end": vulnerable_range_end,
        "timestamp": timestamp,
        "run_id": resolved_run_id,
    }
    written_path = _write_outbox_file(payload)
    logger.info(f"Notification for {owner_name} written to '{written_path}'")
    return {"status": f"Notification for {owner_name} successfully written", "path": written_path}


@click.command()
@click.option("--port", default=PipelineConfig.NOTIFICATION_MCP_PORT, help="Port to run the MCP server on")
def main(port: int):
    mcp.port = port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
