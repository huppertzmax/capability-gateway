"""
Shared runtime helper functions to create agents with MCP gateway connections.
"""

from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.tools import RunContext
from shared.agent_logging import AgentLogger
from shared.config import PipelineConfig
from shared.model_loader import build_chat_model


def _read_prompt_text(prompt_path: Path, logger: logging.Logger) -> str:
    """Read a prompt file from disk with structured error handling."""
    try:
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        logger.error(
            f"System prompt file not found: {prompt_path}",
            exc_info=True,
        )
        raise
    except OSError as exc:
        logger.error(
            f"Failed to read system prompt file: {prompt_path}: {exc}",
            exc_info=True,
        )
        raise

def load_system_prompt(
    system_prompt_path: Path,
    logger: logging.Logger,
    *,
    addendum_path: Path | None = None,
) -> str:
    """
    Load the system prompt text from disk.
    Args:
        system_prompt_path: Filesystem path to the prompt text.
        logger: Logger instance for error reporting.
        addendum_path: Optional path to extra prompt instructions to append.
    Returns:
        str: System prompt text.
    Raises:
        FileNotFoundError: When the prompt file is missing.
        OSError: When the file cannot be read.
    """
    base_prompt = _read_prompt_text(system_prompt_path, logger)
    if addendum_path is None:
        return base_prompt
    addendum_prompt = _read_prompt_text(addendum_path, logger)
    if not addendum_prompt.strip():
        return base_prompt
    if base_prompt.endswith("\n"):
        return f"{base_prompt}{addendum_prompt}"
    return f"{base_prompt}\n{addendum_prompt}"

def create_gateway_mcp_toolset(
    gateway_url: str,
    agent_id: str,
    logger: AgentLogger,
    max_retries: int = 1,
) -> MCPServerStreamableHTTP:
    """
    Build an MCPServerStreamableHTTP toolset that always injects caller_id and logs calls.
    Args:
        gateway_url: Gateway MCP endpoint URL.
        agent_id: Stable agent identifier (used as caller_id).
        logger: AgentLogger for console/file logging.
        max_retries: Maximum number of retries for MCP calls (default: 1).
    Returns:
        MCPServerStreamableHTTP: Configured toolset.
    """

    async def _intercept_tool_call(
        ctx: RunContext[Any],
        call_tool_func: Callable[[str, Dict[str, Any]], Awaitable[Any]],
        name: str,
        tool_args: Dict[str, Any],
    ) -> Any:
        args = dict(tool_args)
        args["caller_id"] = agent_id
        active_run_id = logger.active_run_id
        if not active_run_id:
            raise ValueError(f"Missing active run_id for gateway agent '{agent_id}'.")
        start_ts = time.monotonic()
        start_ts, span_handle = logger.log_mcp_call_started(name, args)
        try:
            result = await call_tool_func(name, args)
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            logger.log_mcp_call_failed(
                name,
                status,
                str(exc),
                start_ts=start_ts,
                span_handle=span_handle,
            )
            if isinstance(exc, AttributeError) and "'str' object has no attribute 'message'" in str(exc):
                raise ModelRetry(
                    "Tool call failed due to malformed backend error payload; verify arguments and retry."
                ) from exc
            raise
        logger.log_mcp_call_finished(
            name,
            status_code=200,
            result=result,
            start_ts=start_ts,
            span_handle=span_handle,
        )
        return result

    agent_timeout = PipelineConfig.GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS
    return MCPServerStreamableHTTP(
        gateway_url,
        process_tool_call=_intercept_tool_call,
        timeout=agent_timeout,
        read_timeout=agent_timeout,
        max_retries=max_retries,
    )

def create_agent_with_mcp_gateway_connection(
    *,
    model_name: str,
    system_prompt: str,
    gateway_url: str,
    agent_id: str,
    agent_logger: AgentLogger,
) -> Agent:
    """
    Construct a pydantic.ai agent with a gateway MCP toolset.
    Args:
        model_name: OpenAI model name to use for the agent.
        system_prompt: System prompt text for the agent.
        gateway_url: Gateway MCP endpoint URL.
        agent_id: Stable agent identifier.
        agent_logger: Agent logger instance for LLM/MCP logging.
    Returns:
        Agent: Configured pydantic.ai agent.
    """
    model = build_chat_model(model_name)
    gateway_toolset = create_gateway_mcp_toolset(
        gateway_url,
        agent_id,
        logger=agent_logger,
        max_retries=3,
    )
    agent = Agent(
        model,
        system_prompt=system_prompt,
        toolsets=[gateway_toolset],
        event_stream_handler=agent_logger.log_llm_event_stream,
        name=agent_id
    )
    return agent
