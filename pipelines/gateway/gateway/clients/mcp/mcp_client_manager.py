"""
Aggregated MCP client manager coordinating multiple server connections.
"""

import logging
from typing import Any, Dict, List, Optional
from gateway.gateway.clients.mcp.mcp_client import MCPClient, ServerConfig, ToolInfo
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-mcp-manager-console",
    file_tag="gateway-mcp-manager-file",
    file_mode="a",
)


class MCPClientManager:
    """
    Manage connections to multiple MCP servers.
    """

    def __init__(self, server_configs: List[ServerConfig]):
        """
        Store server configurations for subsequent connections.
        Args:
            server_configs: Collection of MCP server descriptors.
        """
        self.server_configs = server_configs
        self.clients: Dict[str, MCPClient] = {}

    async def connect_all(self) -> None:
        """
        Connect to each configured MCP server sequentially.
        """
        logger.info("Starting connections to %d servers", len(self.server_configs))
        for config in self.server_configs:
            try:
                client = MCPClient(config)
                await client.connect()
                self.clients[config.name] = client
            except Exception as exc:
                logger.error("[%s] failed to connect: %s", config.name, exc, exc_info=True)
        logger.info("Connected to %d out of %d servers", len(self.clients), len(self.server_configs))

    async def disconnect_all(self) -> None:
        """
        Disconnect from all known MCP servers.
        """
        logger.info("Disconnecting from all servers")
        for client in self.clients.values():
            await client.disconnect()
        self.clients.clear()

    async def get_all_tools(self) -> Dict[str, List[ToolInfo]]:
        """
        Retrieve the tool list from each connected server.
        Returns:
            Dict[str, List[ToolInfo]]: Mapping of server name to tools.
        """
        all_tools: Dict[str, List[ToolInfo]] = {}
        for server_name, client in self.clients.items():
            try:
                tools = await client.get_tools()
                all_tools[server_name] = tools
            except Exception as exc:
                logger.error("Failed to get tools from %s: %s", server_name, exc, exc_info=True)
                all_tools[server_name] = []
        return all_tools

    async def get_tool(self, server_name: str, tool_name: str) -> Optional[ToolInfo]:
        """
        Retrieve metadata for a specific tool on a specific server.
        Args:
            server_name: MCP server identifier.
            tool_name: Tool name to retrieve.
        Returns:
            Optional[ToolInfo]: Tool metadata if found.
        """
        if server_name not in self.clients:
            logger.warning("Server '%s' not connected", server_name)
            return None
        try:
            tools = await self.clients[server_name].get_tools()
            for tool in tools:
                if tool.tool_name == tool_name:
                    return tool
            return None
        except Exception as exc:
            logger.error("Failed to get tool '%s' from %s: %s", tool_name, server_name, exc, exc_info=True)
            return None

    async def execute_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        run_id: str,
    ) -> Any:
        """
        Execute a tool exposed by a specific server.
        Args:
            server_name: MCP server identifier.
            tool_name: Tool name on the server.
            arguments: Tool invocation payload.
            run_id: Stable run identifier propagated to MCP request headers.
        Returns:
            Any: Tool execution response.
        """
        if server_name not in self.clients:
            raise ValueError(f"Server '{server_name}' not connected")
        return await self.clients[server_name].execute_tool(
            tool_name,
            arguments,
            run_id=run_id,
        )


async def initialize_mcp_manager(server_configs: List[ServerConfig]) -> MCPClientManager:
    """
    Convenience helper that constructs and connects an MCPClientManager.
    Args:
        server_configs: List of server configuration descriptors.
    Returns:
        MCPClientManager: Ready-to-use client manager instance.
    """
    manager = MCPClientManager(server_configs)
    await manager.connect_all()
    return manager
