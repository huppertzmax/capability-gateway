"""
Shared A2A AgentExecutor that wires a CrewAI agent class with unified agent logging.
"""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Optional, Type
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, UnsupportedOperationError
from a2a.utils import completed_task, new_text_artifact
from a2a.utils.errors import ServerError
from a2a.utils.message import new_agent_text_message
from shared.agent_logging import AgentLogger, CrewStepLogger, EvaluationRecorder, RunLoggingManager, get_agent_logger
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger
from shared.model_loader import attach_langfuse_callback, set_active_run_id
from shared.run_context import require_run_id

logger = configure_component_logger(
    logger_name=__name__,
    component_group="simple",
    component_name="base_executor",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="simple-base-executor-console",
    file_tag="simple-base-executor-file",
    file_mode="a",
)


class BaseAgentExecutor(AgentExecutor):
    """Generic executor for CrewAI agents."""

    def __init__(
        self,
        agent_cls: Type,
        *,
        agent_id: str,
        model_name: Optional[str] = None,
        architecture: Optional[str] = None,
        result_label: str = "Result",
    ) -> None:
        self.agent_id = agent_id
        self.agent = agent_cls(model_name=model_name, architecture=architecture)
        self.result_label = result_label
        self.agent_logger: AgentLogger = get_agent_logger(agent_id)
        self.crew_step_logger = CrewStepLogger(self.agent_logger)
        self._wire_step_callback()
        runs_root = Path(__file__).resolve().parents[3] / "output" / "runs"
        self.logging_manager = RunLoggingManager(
            agent_logger=self.agent_logger,
            recorder=EvaluationRecorder(
                agent_name=agent_id,
                runs_root=runs_root,
            ),
        )
        logger.debug(
            f"Initialized BaseAgentExecutor agent={self.agent_id} model={model_name or '-'} architecture={architecture or '-'}"
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = context.get_user_input()
        run_id = self._resolve_run_id(context)
        set_active_run_id(run_id)
        self._set_agent_run_id(run_id)
        logger.info(
            f"Execution started agent={self.agent_id} run_id={run_id} task_id={context.task_id or '-'} context_id={context.context_id or '-'}"
        )
        logger.debug(f"Execution prompt size agent={self.agent_id} run_id={run_id} chars={len(query)}")
        result_raw: str = ""
        self.crew_step_logger.start_run()
        try:
            with self.logging_manager.start_run(run_id) as session:
                session.record_prompt(query)
                try:
                    model = getattr(self.agent, "model", None)
                    if model is not None:
                        attach_langfuse_callback(model, run_id)
                    result = await asyncio.wait_for(
                        self.agent.invoke(query),
                        timeout=PipelineConfig.SIMPLE_AGENT_RUN_TIMEOUT_SECONDS,
                    )
                    result_raw = result.raw if hasattr(result, "raw") else str(result)
                    if not str(result_raw).strip():
                        raise ValueError("Invalid response from LLM call - None or empty.")
                    session.record_response(result_raw)
                except asyncio.TimeoutError as exc:
                    result_raw = (
                        f"Agent invocation timed out after "
                        f"{PipelineConfig.SIMPLE_AGENT_RUN_TIMEOUT_SECONDS}s"
                    )
                    session.record_response(result_raw)
                    raise RuntimeError(result_raw) from exc
                except ValueError as exc:
                    result_raw = f"LLM returned empty response; recorded error: {exc}"
                    session.record_response(result_raw)
                    raise RuntimeError(str(exc)) from exc
                except Exception as exc:
                    result_raw = f"Agent invocation failed: {exc}"
                    session.record_response(result_raw)
                    self.crew_step_logger.fail_pending_calls(str(exc))
                    raise
                logger.info(
                    f"Execution finished agent={self.agent_id} run_id={run_id} status=success response_chars={len(result_raw)}"
                )
        except Exception as exc:
            self.crew_step_logger.fail_pending_calls(str(exc))
            logger.error(
                "Agent execution failed agent=%s run_id=%s error=%s",
                self.agent_id,
                run_id,
                exc,
                exc_info=True,
            )
            if not result_raw:
                result_raw = f"Agent invocation failed: {exc}"
        finally:
            self._set_agent_run_id(None)
            set_active_run_id(None)
            logger.debug(f"Execution cleanup completed agent={self.agent_id} run_id={run_id}")
        output = new_text_artifact(self.result_label, result_raw)
        agent_message = new_agent_text_message(
            text=result_raw,
            context_id=context.context_id,
            task_id=context.task_id,
        )
        logger.debug(
            f"Queueing completed task agent={self.agent_id} run_id={run_id} task_id={context.task_id or '-'}"
        )
        await event_queue.enqueue_event(
            completed_task(
                context.task_id,
                context.context_id,
                [output],
                [agent_message],
            )
        )
        logger.info(f"Execution response queued agent={self.agent_id} run_id={run_id}")

    async def cancel(self, request: RequestContext, event_queue: EventQueue) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

    def _resolve_run_id(self, context: RequestContext) -> str:
        """
        Resolve a run identifier for logging and tracing.
        The run_id must be propagated explicitly via request metadata.
        """
        metadata = context.metadata or {}
        return require_run_id(
            metadata.get("run_id"),
            source=f"simple agent '{self.agent_id}' request metadata",
        )

    def _set_agent_run_id(self, run_id: str | None) -> None:
        """Set the active run id on agents that expose a set_run_id hook."""
        setter = getattr(self.agent, "set_run_id", None)
        if callable(setter):
            setter(run_id)

    def _wire_step_callback(self) -> None:
        """Attach the shared Crew step logger when the agent exposes the hook."""
        setter = getattr(self.agent, "set_step_callback", None)
        if callable(setter):
            setter(self.crew_step_logger)
