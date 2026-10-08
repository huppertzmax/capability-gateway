"""
FastAPI application factory for the gateway service.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from gateway.gateway.api import router as api_router
from gateway.gateway.clients.mcp.mcp_client_manager import initialize_mcp_manager
from gateway.gateway.clients.a2a.a2a_client_manager import initialize_a2a_manager
from gateway.gateway.config.config import load_gateway_config
from gateway.gateway.gateway_logging import setup_gateway_logging
from gateway.gateway.services.capability_catalog import CapabilityCatalog
from gateway.gateway.services.access_control import AccessController
from shared.logging_utils import configure_component_logger, get_component_log_path

logger = configure_component_logger(
    logger_name=__name__,
    component_group="gateway",
    component_name="gateway",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="gateway-app-console",
    file_tag="gateway-app-file",
    file_mode="a",
)

acl_policy_config_path = Path(__file__).parent / "config" / "acl_policy_config.json"
gateway_log_path = get_component_log_path("gateway", "gateway")

def create_app(mode: str = "standard") -> FastAPI:
    """
    Create and configure the FastAPI application instance.
    Returns:
        FastAPI: Configured application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Manage application startup and shutdown tasks.
        """
        # Setup gateway logging
        gateway_logger = setup_gateway_logging(gateway_log_path)
        
        # Load configuration
        app.state.config = load_gateway_config(acl_policy_config_path)
        app.state.mcp_manager = None
        app.state.a2a_manager = None
        app.state.capability_catalog = None
        app.state.gateway_logger = gateway_logger

        try:
            # Initialize MCP and A2A managers concurrently
            logger.info("Initializing MCP and A2A managers...")
            mcp_manager, a2a_manager = await asyncio.gather(
                initialize_mcp_manager(app.state.config.mcp_servers),
                initialize_a2a_manager(app.state.config.agents),
            )
            app.state.mcp_manager = mcp_manager
            app.state.a2a_manager = a2a_manager
            logger.info("MCP and A2A managers initialized successfully")

            # Initialize capability catalog
            logger.info("Initializing capability catalog...")
            app.state.capability_catalog = CapabilityCatalog(
                mcp_manager=app.state.mcp_manager,
                agent_manager=app.state.a2a_manager,
                access_controller=AccessController(app.state.config.acl_policy),
                logger=gateway_logger,
                rag_mode=mode
            )
            await app.state.capability_catalog.init_all_capabilities()
            logger.info("Capability catalog initialized successfully")
            
        except Exception as exc:
            logger.error(
                f"Failed to initialize managers and capability catalog: {exc}",
                exc_info=True,
            )

        yield
        
        # Cleanup on shutdown
        if app.state.mcp_manager:
            await app.state.mcp_manager.disconnect_all()
            logger.info("MCP manager disconnected")
        if app.state.a2a_manager:
            await app.state.a2a_manager.disconnect_all()
            logger.info("A2A manager disconnected")

    app = FastAPI(
        title="Gateway Server",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app
