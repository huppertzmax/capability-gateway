"""
Shared logging and run-tracing utilities for all agent pipelines.
Console/file logs stay local, while run/LLM/MCP events are mirrored to Langfuse.
"""

from __future__ import annotations
from dataclasses import dataclass
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterable, Dict, Optional, Set, Tuple
from pydantic_ai.tools import RunContext
from shared.model_loader import LANGFUSE_MANAGER
from shared.logging_utils import configure_component_logger, upsert_file_handler

@dataclass
class RunTokens:
    """Backward-compatible run metadata container (token fields unused)."""

    run_id: str
    agent: str
    tokens_out: int = 0
    tokens_in: int = 0
    prompt_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        """Return run statistics as a serializable dict."""
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "tokens_out": self.tokens_out,
            "tokens_in": self.tokens_in,
            "prompt_count": self.prompt_count,
        }


@dataclass
class PendingToolCall:
    """Container for an in-flight tool call tracked by CrewStepLogger."""

    tool_name: str
    start_ts: float
    span_handle: Optional[Tuple[Any, Any]]


class EvaluationRecorder:
    """Trace persistence for a single agent without local token counting."""

    def __init__(self, agent_name: str, runs_root: Path) -> None:
        self.agent: str = agent_name
        self.runs_root: Path = runs_root
        self.logger: logging.Logger = logging.getLogger(f"agents.eval.{agent_name}")
        self.runs: Set[str] = set()
        self._run_paths: Dict[str, Tuple[Path, Path]] = {}
        self.langfuse = LANGFUSE_MANAGER

    def start_run(self, run_id: str | None = None, run_dir_name: str | None = None) -> Tuple[str, Path]:
        """Create run directory and initialize token counters."""
        if not run_id:
            raise ValueError("run_id is required to start a run")
        dir_name = run_dir_name or run_id
        run_dir = self.runs_root / dir_name / self.agent
        trace_path = run_dir / "trace.jsonl"
        run_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = run_dir / "metrics.json"
        self.runs.add(run_id)
        self._run_paths[run_id] = (trace_path, metrics_path)
        if self.langfuse:
            self.langfuse.ensure_trace(
                run_id,
                name=f"{self.agent}_run",
                metadata={"agent": self.agent, "component": "agent"},
            )
        return run_id, run_dir

    def record_prompt(self, prompt_text: str, run_id: str, agent: str | None = None) -> None:
        """Record an outbound prompt for file trace."""
        self._record(
            run_id=run_id,
            text=prompt_text,
            direction="prompt",
            agent_override=agent,
        )

    def record_response(self, response_text: str, run_id: str, agent: str | None = None) -> None:
        """Record an inbound response for file trace."""
        self._record(
            run_id=run_id,
            text=response_text,
            direction="response",
            agent_override=agent,
        )

    def _record(self, run_id: str, text: str, direction: str, agent_override: str | None) -> None:
        self._require_run(run_id)
        if direction not in {"prompt", "response"}:
            raise ValueError(f"Unsupported direction '{direction}'")
        self._append_trace(run_id, direction, text, agent_override=agent_override)

    def _append_trace(self, run_id: str, direction: str, content: str, *, agent_override: str | None) -> None:
        trace_path, _ = self._require_paths(run_id)
        payload = {
            "run_id": run_id,
            "agent": agent_override or self.agent,
            "direction": direction,
            "content": content,
        }
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        separator_char = "§" if direction == "response" else "-"
        separator = f"\n{separator_char * 100}\n"
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.write(separator)

    def _require_run(self, run_id: str) -> RunTokens:
        if run_id not in self.runs:
            raise ValueError(f"Unknown run_id '{run_id}'. Forgot to call start_run()?")
        return RunTokens(run_id=run_id, agent=self.agent)

    def _require_paths(self, run_id: str) -> Tuple[Path, Path]:
        paths = self._run_paths.get(run_id)
        if paths is None:
            raise ValueError(f"Missing paths for run_id '{run_id}'.")
        return paths


class AgentLogger:
    """Structured logger for MCP-based agents with console/file output."""

    def __init__(
        self,
        agent_name: str,
        log_file: Path | None = None,
        *,
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
    ) -> None:
        """Create dedicated logger with console and optional file handlers."""
        self.agent_name = agent_name
        effective_file_level = console_level if file_level is None else file_level
        self.logger = configure_component_logger(
            logger_name=f"agents.{agent_name}",
            component_group="agents",
            component_name=agent_name,
            console_level=console_level,
            file_level=effective_file_level,
            console_tag="agent-console",
            file_tag="agent-component-file",
            file_mode="a",
        )
        self._file_level = effective_file_level
        self._active_run_id: str | None = None
        self._langfuse = LANGFUSE_MANAGER
        self._run_span_id: str | None = None
        if log_file is not None:
            self.set_run_log_file(log_file)

    @property
    def active_run_id(self) -> str | None:
        """Return the run identifier currently bound to this logger."""
        return self._active_run_id

    def set_active_run(self, run_id: str) -> None:
        """Bind a run identifier for downstream logging."""
        self._active_run_id = run_id
        self.logger.debug("Logger bound to run_id=%s", run_id)
        if self._langfuse and run_id:
            self._run_span_id = run_id
            self._langfuse.start_run_span(
                run_id,
                name=f"{self.agent_name}_run",
                metadata={"agent": self.agent_name, "component": "agent"},
            )

    def clear_active_run(self) -> None:
        """Clear the active run identifier after a run completes."""
        self.logger.debug("Logger clearing run_id=%s", self._active_run_id)
        if self._langfuse and self._run_span_id:
            self._langfuse.end_run_span(self._run_span_id)
            self._run_span_id = None
        self._active_run_id = None

    def set_run_log_file(self, log_file: Path) -> None:
        """Swap the file handler to the current run log."""
        upsert_file_handler(
            logger=self.logger,
            tag="agent-run-file",
            log_file=log_file,
            level=self._file_level,
            mode="a",
            formatter=self._file_formatter(),
        )

    def log_run_started(self, run_id: str) -> float:
        """Log run start and return a monotonic start timestamp."""
        self.logger.info("AGENT → %s run=%s", self.agent_name, run_id)
        self.logger.debug("Run started agent=%s run_id=%s", self.agent_name, run_id)
        return time.monotonic()

    def log_run_finished(
        self,
        *,
        start_ts: float | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log run completion with status and duration."""
        duration_ms = self._duration_ms(start_ts)
        if success:
            self.logger.info("AGENT ← %s success%s", self.agent_name, self._duration_suffix(duration_ms))
            self.logger.debug(
                "Run finished agent=%s status=success duration_ms=%s",
                self.agent_name,
                duration_ms,
            )
            return
        self.logger.error(
            "AGENT ✕ %s failed%s error=%s",
            self.agent_name,
            self._duration_suffix(duration_ms),
            error or "-",
        )

    def log_llm_request(self, prompt: str) -> None:
        """Log the start of an LLM call without emitting the prompt content."""
        self.logger.info("LLM → start run")

    def log_llm_response(self, response: str, *, duration_ms: float | None = None) -> None:
        """Log the end of an LLM call without emitting the response content."""
        suffix = self._duration_suffix(duration_ms)
        self.logger.info("LLM ← done%s", suffix)
        if self._langfuse and self._active_run_id:
            self._langfuse.log_event(
                run_id=self._active_run_id,
                name=f"{self.agent_name}.llm_response",
                output_payload=response,
                metadata={"direction": "response", "duration_ms": duration_ms},
            )

    async def log_llm_event_stream(self, ctx: RunContext[Any], stream: AsyncIterable[Any]) -> None:
        """Consume a pydantic.ai event stream; keep logs minimal (no duplicate MCP logs)."""
        async for event in stream:
            event_kind = getattr(event, "event_kind", "unknown")
            if event_kind == "final_result":
                self.logger.debug("LLM final_result: %s", event)

    def log_mcp_call_started(self, tool_name: str, args: Any) -> Tuple[float, Optional[Tuple[Any, Any]]]:
        """Log before calling an MCP tool and return start time plus span handle."""
        self.logger.info("MCP → %s", tool_name)
        self.logger.debug("MCP call start tool=%s args=%s", tool_name, args)
        span_handle = None
        if self._langfuse and self._active_run_id:
            span_handle = self._langfuse.start_span(
                run_id=self._active_run_id,
                name=f"mcp:{tool_name}",
                input_payload=args,
                metadata={"status": "start", "tool": tool_name},
            )
        return time.monotonic(), span_handle

    def log_mcp_call_finished(
        self,
        tool_name: str,
        status_code: int,
        *,
        result: Any = None,
        start_ts: Optional[float] = None,
        span_handle: Optional[Tuple[Any, Any]] = None,
    ) -> None:
        """Log after a successful MCP tool call."""
        duration_ms = self._duration_ms(start_ts)
        self.logger.info("MCP ← %s status=%s%s", tool_name, status_code, self._duration_suffix(duration_ms))
        self.logger.debug(
            "MCP call end tool=%s status=%s duration_ms=%s result=%s",
            tool_name,
            status_code,
            duration_ms,
            result,
        )
        if self._langfuse and self._active_run_id:
            self._langfuse.end_span(
                span_handle,
                output=result,
                metadata={"status": "success", "status_code": status_code, "duration_ms": duration_ms},
            )

    def log_mcp_call_failed(
        self,
        tool_name: str,
        status_code: Optional[int],
        error: str,
        *,
        start_ts: Optional[float] = None,
        span_handle: Optional[Tuple[Any, Any]] = None,
    ) -> None:
        """Log a failed MCP tool call with error context."""
        duration_ms = self._duration_ms(start_ts)
        status_part = status_code if status_code is not None else "-"
        self.logger.error("MCP ✕ %s status=%s%s error=%s", tool_name, status_part, self._duration_suffix(duration_ms), error)
        if self._langfuse and self._active_run_id:
            self._langfuse.end_span(
                span_handle,
                output={"error": error},
                metadata={"status": "error", "status_code": status_part, "duration_ms": duration_ms},
            )

    def log_exception(self, message: str, *, exc: BaseException) -> None:
        """Log an exception with stack trace."""
        self.logger.error("%s: %s", message, exc, exc_info=True)

    def _file_formatter(self) -> logging.Formatter:
        return logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s")

    def _duration_ms(self, start_ts: Optional[float]) -> Optional[float]:
        return None if start_ts is None else (time.monotonic() - start_ts) * 1000

    def _duration_suffix(self, duration_ms: Optional[float]) -> str:
        return "" if duration_ms is None else f" time={duration_ms:.1f}ms"


class CrewStepLogger:
    """
    CrewAI step callback adapter that emits MCP-style flow logs via AgentLogger.
    The callback receives mixed event types; event matching is intentionally duck-typed.
    """

    def __init__(self, agent_logger: AgentLogger) -> None:
        self.agent_logger = agent_logger
        self._pending_calls: list[PendingToolCall] = []

    def start_run(self) -> None:
        """Reset pending tool-call state at the beginning of each run."""
        self._pending_calls = []

    def __call__(self, event: Any) -> None:
        """Handle CrewAI step callback events."""
        if self._is_agent_action(event):
            self._on_action(event)
            return
        if self._is_tool_result(event):
            self._on_result(event)

    def fail_pending_calls(self, error: str) -> None:
        """Close any in-flight spans as failed when a run aborts unexpectedly."""
        while self._pending_calls:
            pending = self._pending_calls.pop()
            self.agent_logger.log_mcp_call_failed(
                pending.tool_name,
                None,
                error,
                start_ts=pending.start_ts,
                span_handle=pending.span_handle,
            )

    def _on_action(self, event: Any) -> None:
        tool_name = str(getattr(event, "tool", "") or "").strip()
        if not tool_name:
            return
        parsed_args = self._parse_tool_input(getattr(event, "tool_input", None))
        start_ts, span_handle = self.agent_logger.log_mcp_call_started(tool_name, parsed_args)
        self._pending_calls.append(
            PendingToolCall(
                tool_name=tool_name,
                start_ts=start_ts,
                span_handle=span_handle,
            )
        )

    def _on_result(self, event: Any) -> None:
        if not self._pending_calls:
            return
        pending = self._pending_calls.pop()
        result = getattr(event, "result", None)
        self.agent_logger.log_mcp_call_finished(
            pending.tool_name,
            status_code=200,
            result=result,
            start_ts=pending.start_ts,
            span_handle=pending.span_handle,
        )

    def _is_agent_action(self, event: Any) -> bool:
        return hasattr(event, "tool") and hasattr(event, "tool_input")

    def _is_tool_result(self, event: Any) -> bool:
        return hasattr(event, "result") and hasattr(event, "result_as_answer")

    def _parse_tool_input(self, tool_input: Any) -> Any:
        if not isinstance(tool_input, str):
            return tool_input
        stripped = tool_input.strip()
        if not stripped:
            return {}
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return {"raw": stripped}


class RunLoggingSession:
    """Run-scoped helper combining recorder and logger."""

    def __init__(
        self,
        run_id: str,
        recorder: EvaluationRecorder,
        agent_logger: AgentLogger,
    ) -> None:
        self.run_id = run_id
        self.recorder = recorder
        self.agent_logger = agent_logger
        self._llm_start_ts: float | None = None
        self._run_start_ts: float | None = None

    def record_prompt(self, prompt_text: str) -> None:
        """Record an outbound LLM prompt and log it."""
        self.recorder.record_prompt(prompt_text, self.run_id)
        self.agent_logger.log_llm_request(prompt_text)
        self._llm_start_ts = time.monotonic()

    def record_response(self, response_text: str) -> None:
        """Record an inbound LLM response and log it."""
        self.recorder.record_response(response_text, self.run_id)
        duration_ms = None
        if self._llm_start_ts is not None:
            duration_ms = (time.monotonic() - self._llm_start_ts) * 1000
        self.agent_logger.log_llm_response(response_text, duration_ms=duration_ms)
        self._llm_start_ts = None

    def __enter__(self) -> "RunLoggingSession":
        self.agent_logger.set_active_run(self.run_id)
        self._run_start_ts = self.agent_logger.log_run_started(self.run_id)
        # LANGFUSE_MANAGER.log_event(
        #     run_id=self.run_id,
        #     name=f"{self.agent_logger.agent_name}.run_start",
        #     metadata={"component": "agent", "agent": self.agent_logger.agent_name},
        # )
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.agent_logger.log_exception("Run failed", exc=exc)
            LANGFUSE_MANAGER.log_event(
                run_id=self.run_id,
                name=f"{self.agent_logger.agent_name}.run_error",
                output_payload=str(exc),
                metadata={"component": "agent", "agent": self.agent_logger.agent_name},
            )
            self.agent_logger.log_run_finished(
                start_ts=self._run_start_ts,
                success=False,
                error=str(exc),
            )
        else:
            LANGFUSE_MANAGER.log_event(
                run_id=self.run_id,
                name=f"{self.agent_logger.agent_name}.run_complete",
                metadata={"component": "agent", "agent": self.agent_logger.agent_name},
            )
            self.agent_logger.log_run_finished(
                start_ts=self._run_start_ts,
                success=True,
            )
        self.agent_logger.clear_active_run()
        return False


class RunLoggingManager:
    """Wrapper combining AgentLogger and EvaluationRecorder per run."""

    def __init__(
        self,
        agent_logger: AgentLogger,
        recorder: EvaluationRecorder,
    ) -> None:
        self.agent_logger = agent_logger
        self.recorder = recorder

    def start_run(self, run_id: str, *, log_file_name: str = "log.txt") -> RunLoggingSession:
        """Prepare logging artifacts for a run and return a session."""
        run_id, run_dir = self.recorder.start_run(run_id=run_id, run_dir_name=run_id)
        log_file = run_dir / log_file_name
        self.agent_logger.set_run_log_file(log_file)
        return RunLoggingSession(
            run_id=run_id,
            recorder=self.recorder,
            agent_logger=self.agent_logger,
        )


def get_agent_logger(agent_name: str, log_file: Path | None = None) -> AgentLogger:
    """Create an AgentLogger with optional file handler."""
    return AgentLogger(agent_name, log_file)


__all__ = [
    "AgentLogger",
    "CrewStepLogger",
    "EvaluationRecorder",
    "PendingToolCall",
    "RunLoggingManager",
    "RunLoggingSession",
    "RunTokens",
    "get_agent_logger",
]
