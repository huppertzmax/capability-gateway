"""
Low-level MCP client responsible for managing a single server connection.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from shared.logging_utils import configure_component_logger
from shared.run_context import RUN_ID_HEADER, require_run_id

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-mcp-client-console",
    file_tag="gateway-mcp-client-file",
    file_mode="a",
)

MCP_ERROR_KEYS_TO_SCRUB = ("tool_name", "server_name", "agent_id", "skill_id", "kind")


class McpToolCallError(RuntimeError):
    """Raised when an MCP server reports a tool execution error via isError."""

    def __init__(self, structured_content: Any):
        """
        Initialize error with the structured content returned by the MCP server.
        Args:
            structured_content: Structured error content returned by MCP.
        """
        super().__init__("MCP tool call returned an error")
        self.structured_content = structured_content


@dataclass
class ServerConfig:
    """
    Configuration describing how to connect to an MCP server.
    """
    name: str
    url: str
    transport_type: str = "http"


@dataclass
class ToolInfo:
    """
    Metadata returned by an MCP server when listing tools.
    """
    server_name: str
    tool_name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]


class MCPClient:
    """
    Manage the lifecycle of a single MCP server connection.
    """

    def __init__(self, config: ServerConfig):
        """
        Store configuration for future connection attempts.
        Args:
            config: Server configuration descriptor.
        """
        self.config = config
        self.session: Optional[ClientSession] = None
        self.session_context = None
        self.transport_context = None
        self._read_stream = None
        self._write_stream = None

    async def connect(self) -> None:
        """
        Establish a connection and initialize the remote session.
        """
        try:
            if self.config.transport_type == "http":
                logger.info("[%s] Connecting to %s", self.config.name, self.config.url)
                self.transport_context = streamablehttp_client(self.config.url)
                self._read_stream, self._write_stream, _ = await self.transport_context.__aenter__()
                self.session_context = ClientSession(self._read_stream, self._write_stream)
                self.session = await self.session_context.__aenter__()
                logger.info("[%s] Initializing session...", self.config.name)
                await self.session.initialize()
                logger.info("[%s] Connected and initialized", self.config.name)
            elif self.config.transport_type == "stdio":
                raise NotImplementedError("Stdio transport not yet implemented")
            elif self.config.transport_type == "sse":
                raise NotImplementedError("SSE transport not yet implemented")
            else:
                raise ValueError(f"Unknown transport type: {self.config.transport_type}")
        except Exception as exc:
            logger.error("[%s] Failed to connect: %s", self.config.name, exc, exc_info=True)
            await self._cleanup()
            raise

    async def _cleanup(self) -> None:
        """
        Close any open contexts quietly.
        """
        def _is_cross_task_cancel_scope_exit(exc: Exception) -> bool:
            """
            AnyIO raises this during teardown if __aenter__/__aexit__ run on different tasks.
            This can happen during normal app shutdown and is safe to ignore.
            """
            return isinstance(exc, RuntimeError) and "exit cancel scope in a different task" in str(exc).lower()

        try:
            if self.session_context and self.session:
                await self.session_context.__aexit__(None, None, None)
        except Exception as exc:
            if _is_cross_task_cancel_scope_exit(exc):
                logger.info("[%s] Session context already torn down during shutdown", self.config.name)
            else:
                logger.error("[%s] Error exiting session context: %s", self.config.name, exc, exc_info=True)
        try:
            if self.transport_context:
                await self.transport_context.__aexit__(None, None, None)
        except Exception as exc:
            if _is_cross_task_cancel_scope_exit(exc):
                logger.info("[%s] Transport context already torn down during shutdown", self.config.name)
            else:
                logger.error("[%s] Error exiting transport context: %s", self.config.name, exc, exc_info=True)
        finally:
            self.session = None
            self.session_context = None
            self.transport_context = None
            self._read_stream = None
            self._write_stream = None

    async def disconnect(self) -> None:
        """
        Public hook to disconnect from the remote server.
        """
        logger.info("[%s] Disconnecting...", self.config.name)
        await self._cleanup()
        logger.info("[%s] Disconnected", self.config.name)

    async def get_tools(self) -> List[ToolInfo]:
        """
        Fetch the list of tools exposed by the server.
        Returns:
            List[ToolInfo]: Tool metadata records.
        """
        if not self.session:
            raise RuntimeError(f"Not connected to {self.config.name}")
        try:
            tools = []
            response = await self.session.list_tools()
            for tool in response.tools:
                tools.append(
                    ToolInfo(
                        server_name=self.config.name,
                        tool_name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema or {},
                        output_schema=tool.outputSchema or {},
                    )
                )
            logger.info("[%s] Retrieved %d tools", self.config.name, len(tools))
            return tools
        except Exception as exc:
            logger.error("[%s] Failed to get tools: %s", self.config.name, exc, exc_info=True)
            raise

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        run_id: str,
    ) -> Any:
        """
        Execute a particular tool on the remote server.
        Args:
            tool_name: Tool identifier.
            arguments: Arguments for the tool invocation.
            run_id: Stable run identifier propagated through MCP request headers.
        Returns:
            Any: Response from the remote server.
        """
        if not self.session:
            raise RuntimeError(f"Not connected to {self.config.name}")
        resolved_run_id = require_run_id(
            run_id,
            source=f"gateway MCP client '{self.config.name}' execute_tool argument",
        )
        try:
            headers = {RUN_ID_HEADER: resolved_run_id}
            async with streamablehttp_client(self.config.url, headers=headers) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as run_bound_session:
                    await run_bound_session.initialize()
                    response = await run_bound_session.call_tool(tool_name, arguments)
            logger.info("[%s] Executed tool '%s'", self.config.name, tool_name)
            if response.isError:
                error_payload: Any = response.structuredContent
                if error_payload is None:
                    try:
                        content_blocks = []
                        for block in (response.content or [])[:8]:
                            if hasattr(block, "model_dump"):
                                content_blocks.append(block.model_dump())
                            else:
                                content_blocks.append(str(block))
                        if content_blocks:
                            error_payload = {"content": content_blocks}
                    except Exception:
                        pass
                logger.error(
                    "[%s] Tool '%s' execution error: %s",
                    self.config.name,
                    tool_name,
                    error_payload,
                )
                raise McpToolCallError(error_payload)
            return response.structuredContent
        except Exception as exc:
            logger.error("[%s] Failed to execute tool '%s': %s", self.config.name, tool_name, exc, exc_info=True)
            raise
