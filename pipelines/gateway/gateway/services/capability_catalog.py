"""
Unified capability catalog for MCP tools and agent skills.
"""

from __future__ import annotations

import logging
from uuid import uuid4
from typing import Any, Dict, List
from dotenv import load_dotenv
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger
from shared.model_loader import resolve_run_id, set_active_run_id
from shared.run_context import require_run_id
from gateway.gateway.clients.mcp.mcp_client_manager import MCPClientManager
from gateway.gateway.clients.a2a.a2a_client_manager import A2AClientManager
from gateway.gateway.gateway_logging import GatewayLogger
from gateway.gateway.services.capability_mapper import mcp_tool_to_capability, a2a_skill_to_capability, capability_to_summary, capability_to_detailed_summary
from gateway.gateway.services.capability_schemas import Capability, CapabilitySummary, CapabilityDetail, CapabilityExecutionResult
from gateway.gateway.services.access_control import AccessController
from gateway.vectorstore.qdrant_vectorstore_client import QdrantVectorStoreClient

load_dotenv()
logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-capability-catalog-console",
    file_tag="gateway-capability-catalog-file",
    file_mode="a",
)


class CapabilityCatalogError(RuntimeError):
    """Base error raised by the capability catalog."""


class CapabilityNotFound(CapabilityCatalogError):
    """Raised when a capability cannot be resolved."""


class CapabilityNotAllowed(CapabilityCatalogError):
    """Raised when a caller is not allowed to access a capability."""


class CapabilityInputError(CapabilityCatalogError):
    """Raised when capability arguments are missing or invalid."""


class CapabilityCatalog:
    """
    Catalog and execution facade for MCP tools and agent skills.
    """

    def __init__(
        self,
        mcp_manager: MCPClientManager,
        agent_manager: A2AClientManager,
        access_controller: AccessController,
        logger: GatewayLogger | None = None,
        rag_mode: str = "standard",
    ):
        """
        Initialize the catalog with MCP manager and agent registry.
        Args:
            mcp_manager: Connected MCP manager for tool discovery/execution.
            agent_manager: Registry for agent base URLs.
            access_controller: Access control policy enforcer.
            logger: Optional GatewayLogger instance for structured logging.
        """
        self.mcp_manager = mcp_manager
        self.agent_manager = agent_manager
        self.capabilities = None
        self._agent_timeout_seconds = PipelineConfig.GATEWAY_AGENT_HTTP_TIMEOUT_SECONDS
        self.access_controller = access_controller
        self.rag_mode = rag_mode
        if self.rag_mode == "RAG":
            self.vector_store_client = QdrantVectorStoreClient()
            self._vector_index_ready = False
            self._initialization_run_id = f"gateway-rag-init|{uuid4()}"
        self.gateway_logger = logger

    async def init_all_capabilities(self) -> None:
        """
        Initialize and cache all capabilities from MCP servers and agents.
        """
        all_agent_capabilities = [
            a2a_skill_to_capability(agent_name, skill) 
            for agent_name, agent_skills in (await self.agent_manager.get_all_agent_skills()).items() 
            for skill in agent_skills
        ]        
        all_mcp_capabilities = [
            mcp_tool_to_capability(server_name, tool) 
            for server_name, tools_list in (await self.mcp_manager.get_all_tools()).items() 
            for tool in tools_list
        ]
        self.capabilities = all_agent_capabilities + all_mcp_capabilities
        if self.rag_mode == "RAG":
            self._vector_index_ready = False
            self._ensure_vector_index(run_id=self._initialization_run_id, reason="startup_initialization")

    async def list_capabilities(
        self,
        caller_agent_name: str,
    ) -> List[CapabilitySummary]:
        """
        List all tool and agent capabilities with optional access filtering.
        Returns:
            List[CapabilitySummary]: Unified capability summaries.
        """
        if self.capabilities is None:
            await self.init_all_capabilities()
        
        start_ts = self.gateway_logger.log_list_capabilities_request(caller_agent_name) if self.gateway_logger else None

        allowed_capabilities = [
            capability for capability in self.capabilities
            if self.access_controller.is_agent_allowed(caller_agent_name, capability)
        ]
        result = [capability_to_summary(cap) for cap in allowed_capabilities]

        if self.gateway_logger:
            self.gateway_logger.log_list_capabilities_response(
                caller_agent_name,
                len(result),
                start_ts=start_ts,
            )

        return result
    
    async def get_capability(
        self,
        capability_id: str,
        caller_agent_name: str,
    ) -> Capability:
        """
        Retrieve a tool or agent capability by its identifier.
        Args:
            capability_id: Capability identifier.
            caller_agent_name: Caller agent name for access control.
        Returns:
            Optional[Capability]: Resolved capability or None.
        Raises:
            CapabilityNotFound: When the capability cannot be resolved.
            CapabilityNotAllowed: When access is denied.
            """
        start_ts = self.gateway_logger.log_get_capability_request(capability_id, caller_agent_name) if self.gateway_logger else None

        capability = next((cap for cap in (self.capabilities or []) if cap.capability_id == capability_id), None)
        if capability is None:
            if self.gateway_logger:
                self.gateway_logger.log_capability_not_found(capability_id, caller_agent_name)
            raise CapabilityNotFound(f"Capability '{capability_id}' is not defined")

        if not self.access_controller.is_agent_allowed(caller_agent_name, capability):
            if self.gateway_logger:
                self.gateway_logger.log_access_denied(
                    capability_id,
                    caller_agent_name,
                    "Access control policy violation",
                )
            raise CapabilityNotAllowed(f"Capability '{capability_id}' is not allowed by agent '{caller_agent_name}'")

        if self.gateway_logger:
            self.gateway_logger.log_get_capability_response(
                capability_id,
                caller_agent_name,
                capability.source_name,
                capability.capability_name,
                capability.kind,
                start_ts=start_ts,
            )

        return capability

    async def retrieve_relevant_capabilities(
        self,
        text: str,
        caller_agent_name: str,
        top_k: int = 3,
    ) -> List[CapabilitySummary]:
        if top_k <= 0:
            return []

        if self.capabilities is None:
            await self.init_all_capabilities()

        if self.rag_mode != "RAG":
            raise CapabilityCatalogError("Relevant capability search is only available in RAG mode.")
        if not self._vector_index_ready:
            active_run_id = require_run_id(
                resolve_run_id(None),
                source="gateway capability catalog RAG runtime context",
            )
            self._ensure_vector_index(run_id=active_run_id, reason="runtime_rebuild")

        # NOTE: In production, this would suck, but for small codebases it's a quick fix for less re-prompting (which may be a lot more costly than comparing all capabilities!)
        search_k = len(self.capabilities)
        results = self.vector_store_client.search(query=text, top_k=search_k)

        matched: List[Capability] = []

        for r in results:
            payload = r.get("payload", {})
            capability_id = payload.get("capability_id")
            capability = Capability(**payload)

            if not self.access_controller.is_agent_allowed(caller_agent_name, capability):
                logger.debug(
                    f"Capability '{capability_id}' is not allowed by agent '{caller_agent_name}'")
                continue

            matched.append(capability)

        result = [capability_to_summary(cap) for cap in matched[:top_k]]

        return result

    def _ensure_vector_index(self, *, run_id: str, reason: str) -> None:
        """Build the RAG vector index once for a specific run context."""
        if self.rag_mode != "RAG":
            return
        if self._vector_index_ready:
            return
        resolved_run_id = require_run_id(run_id, source=f"gateway capability catalog {reason}")
        previous_run_id = resolve_run_id(None)
        set_active_run_id(resolved_run_id)
        try:
            logger.info(
                "Building RAG vector index reason=%s run_id=%s",
                reason,
                resolved_run_id,
            )
            self.vector_store_client.upsert_capabilities(self.capabilities or [])
            self._vector_index_ready = True
        finally:
            set_active_run_id(previous_run_id)


    async def get_capability_details(
        self,
        capability_id: str,
        caller_agent_name: str,
    ) -> CapabilityDetail:
        """
        Retrieve tool or agent capability details with optional access filtering.
        Args:
            capability_id: Capability identifier.
            caller_agent_name: Caller agent name for access control.
        Returns:
            CapabilityDetail: Unified capability details.
        Raises:
            CapabilityNotFound: When the capability cannot be resolved.
            CapabilityNotAllowed: When access is denied.
        """
        return capability_to_detailed_summary(await self.get_capability(capability_id, caller_agent_name))

    async def execute_capability(
        self,
        capability_id: str,
        arguments: Dict[str, Any],
        caller_agent_name: str,
        run_id: str,
    ) -> CapabilityExecutionResult:
        """
        Execute a tool or agent capability with optional access filtering.
        Args:
            capability_id: Capability identifier.
            arguments: Execution arguments payload.
            caller_agent_name: Caller name for agent requests.
        Returns:
            CapabilityExecutionResult: Execution payload with resolved kind.
        Raises:
            CapabilityNotFound: When the capability cannot be resolved.
            CapabilityNotAllowed: When access is denied.
            CapabilityInputError: When required inputs are missing.
        """
        start_ts = self.gateway_logger.log_execute_capability_request(capability_id, caller_agent_name, arguments) if self.gateway_logger else None
        arguments = arguments or {}
        run_id = require_run_id(run_id, source="gateway execute_capability request")
        logger.info("CapabilityCatalog.execute_capability run_id=%s capability=%s", run_id, capability_id)
        
        try:
            capability = await self.get_capability(capability_id, caller_agent_name)
            if capability.kind == "agent" and capability.input_type == "text":
                text_value = arguments.get("text")
                if not isinstance(text_value, str) or not text_value.strip():
                    raise CapabilityInputError(f"Capability '{capability_id}' requires a non-empty 'text' argument.")
            if capability.kind == "tool":
                result = await self.mcp_manager.execute_tool(
                    server_name=capability.source_name,
                    tool_name=capability.capability_name,
                    arguments=arguments,
                    run_id=run_id,
                )
            elif capability.kind == "agent":
                result = await self.agent_manager.execute_agent_skill(
                    agent_name=capability.source_name,
                    agent_skill=capability.capability_name,
                    arguments=arguments,
                    run_id=run_id,
                )

            if self.gateway_logger:
                self.gateway_logger.log_execute_capability_success(
                    capability_id,
                    caller_agent_name,
                    capability.source_name,
                    capability.capability_name,
                    capability.kind,
                    result,
                    start_ts=start_ts,
                )

            return CapabilityExecutionResult(
                capability_id=capability_id,
                kind=capability.kind,
                result=result,
            )
        except (CapabilityNotFound, CapabilityNotAllowed):
            raise
        except Exception as exc:
            if self.gateway_logger:
                self.gateway_logger.log_execute_capability_error(
                    capability_id,
                    caller_agent_name,
                    str(exc),
                    start_ts=start_ts,
                )
            raise
