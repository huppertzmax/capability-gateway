"""
FastAPI HTTP layer for exposing agent skills.
"""

from __future__ import annotations
from pathlib import Path
import os
from typing import Any, Callable, Optional
os.environ["A2A_TELEMETRY_DISABLED"] = "true"
from starlette.applications import Starlette
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import InternalError, Task, UnsupportedOperationError
from a2a.utils import completed_task, new_text_artifact
from a2a.utils.errors import ServerError
from a2a.utils.message import new_agent_text_message
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStreamableHTTP
from gateway.agents.agent_card_config import load_agent_card_config
from shared.agent_logging import AgentLogger, EvaluationRecorder, RunLoggingManager
from shared.model_loader import set_active_run_id
from shared.run_context import RUN_ID_HEADER, require_run_id


class GenericAgentExecutor(AgentExecutor):
    """Generic Agent Executor."""

    def __init__(
        self,
        agent: Agent,
        agent_logger: AgentLogger,
        runs_root: Path | None,
        output_validator: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.agent: Agent = agent
        resolved_root = runs_root or Path(__file__).resolve().parents[2] / "output" / "runs"
        self.logging_manager: RunLoggingManager = RunLoggingManager(
            agent_logger=agent_logger,
            recorder=EvaluationRecorder(
                agent_name=agent.name,
                runs_root=resolved_root,
            ),
        )
        self.output_validator = output_validator

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        try:
            user_prompt = context.get_user_input()
            metadata = context.metadata or {}
            run_id = require_run_id(
                metadata.get("run_id"),
                source=f"gateway agent '{self.agent.name}' request metadata",
            )
            set_active_run_id(run_id)
            self._bind_run_id_header(run_id)
            with self.logging_manager.start_run(run_id) as session:
                session.record_prompt(user_prompt)
                result = await self.agent.run(user_prompt)
                response = result.response.text
                if self.output_validator:
                    response = self.output_validator(response)
                session.record_response(response)
        except Exception as exc:
            raise ServerError(error=InternalError(message=f"Error invoking agent: {exc}")) from exc
        finally:
            self._unbind_run_id_header()
            set_active_run_id(None)
        output = new_text_artifact('Agent Result', response)
        agent_message = new_agent_text_message(
            text=response,
            context_id=context.context_id,
            task_id=context.task_id,
        )
        await event_queue.enqueue_event(
            completed_task(
                context.task_id,
                context.context_id,
                [output],
                [agent_message],
            )
        )
    
    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

    def _bind_run_id_header(self, run_id: str) -> None:
        """Bind X-Run-Id on all MCP HTTP toolsets for the current run."""
        for toolset in self.agent.toolsets:
            if isinstance(toolset, MCPServerStreamableHTTP):
                headers = dict(toolset.headers or {})
                headers[RUN_ID_HEADER] = run_id
                toolset.headers = headers

    def _unbind_run_id_header(self) -> None:
        """Remove X-Run-Id from MCP HTTP toolsets after run completion."""
        for toolset in self.agent.toolsets:
            if isinstance(toolset, MCPServerStreamableHTTP):
                headers = dict(toolset.headers or {})
                if RUN_ID_HEADER in headers:
                    headers.pop(RUN_ID_HEADER)
                toolset.headers = headers if headers else None


def create_agent_executor(
    agent: Agent,
    agent_logger: AgentLogger,
    runs_root: Path | None,
) -> AgentExecutor:
    """
    Create a GenericAgentExecutor for the given agent.
    Args:
        agent (Agent): The agent to wrap.
        agent_logger (AgentLogger): The logger for the agent.
        runs_root: Base directory for per-run outputs; defaults to project_root/output/runs when None.
    Returns:
        AgentExecutor: The created executor.
    """
    return GenericAgentExecutor(
        agent=agent,
        agent_logger=agent_logger,
        runs_root=runs_root,
    )

def create_a2a_agent_app(agent_card_config_path: str, agent_executor: AgentExecutor, override_url: str | None = None) -> Starlette:
    """
    Create an A2A Starlette Application for an agent.
    Returns:
        A2AStarletteApplication: Configured app.
    """
    agent_card = load_agent_card_config(agent_card_config_path)
    if override_url:
        agent_card = agent_card.model_copy(update={"url": override_url})

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )
    return server.build()
