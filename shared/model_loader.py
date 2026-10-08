"""
Shared model and tracing utilities for all pipelines.
Provides unified OpenAI client creation, Langfuse bootstrap, and model validation.
"""
import hashlib
import logging
from typing import Any, Dict, Optional, Tuple
from crewai import LLM
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from langfuse import Langfuse
from langfuse.openai import AsyncOpenAI
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
from openinference.instrumentation.crewai import CrewAIInstrumentor
from shared.config import PipelineConfig
from shared.logging_utils import configure_component_logger
from shared.run_context import clean_run_id

logger = configure_component_logger(
    logger_name=__name__,
    component_group="shared",
    component_name="model_loader",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    console_tag="shared-model-loader-console",
    file_tag="shared-model-loader-file",
    file_mode="a",
)

ALLOWED_MODELS: Tuple[str, ...] = ("gpt-5", "gpt-5-mini", "gpt-5-nano")

_CREWAI_RUNTIME_PATCHED = False


def _patch_crewai_runtime() -> None:
    """
    Apply small runtime patches to CrewAI to avoid hard-failing on transient issues.

    Motivation:
    - CrewAI's get_llm_response raises ValueError on empty responses, which aborts the whole agent loop.
      Returning an empty string instead lets CrewAI's parser raise OutputParserError, which triggers its built-in retry loop.
    - CrewAI's MCPClient cleanup/disconnect can raise during anyio task-group teardown, which can cascade into noisy failures.
      Cleanup should be best-effort.
    """
    global _CREWAI_RUNTIME_PATCHED
    if _CREWAI_RUNTIME_PATCHED:
        return

    try:
        from crewai.agents import crew_agent_executor as crew_agent_executor_module
        from crewai.utilities import agent_utils as agent_utils_module
    except Exception:
        logger.debug("CrewAI runtime patch skipped (import failed)", exc_info=True)
        return

    original_get_llm_response = agent_utils_module.get_llm_response

    def resilient_get_llm_response(*args: Any, **kwargs: Any) -> str:
        try:
            return original_get_llm_response(*args, **kwargs)
        except ValueError as exc:
            message = str(exc)
            if "None or empty" in message:
                return ""
            raise
        except Exception as exc:
            msg = str(exc).lower()
            permanent_markers = ("unauthorized", "forbidden", "authentication", "api key", "invalid api key")
            if any(marker in msg for marker in permanent_markers):
                raise
            if "timeout" in msg or "timed out" in msg or "connection" in msg:
                logger.debug("Suppressed transient LLM call error for retry: %s", exc, exc_info=True)
                return ""
            raise

    agent_utils_module.get_llm_response = resilient_get_llm_response  # type: ignore[assignment]
    crew_agent_executor_module.get_llm_response = resilient_get_llm_response  # type: ignore[assignment]

    try:
        from contextlib import AsyncExitStack
        from crewai.mcp.client import MCPClient
        from crewai.tools.mcp_native_tool import MCPNativeTool

        async def _safe_aclose(stack: AsyncExitStack) -> None:
            try:
                await stack.aclose()
            except Exception:
                logger.debug("Suppressed MCP AsyncExitStack close error", exc_info=True)

        async def patched_cleanup_on_error(self: MCPClient) -> None:  # type: ignore[name-defined]
            await _safe_aclose(self._exit_stack)
            self._session = None
            self._initialized = False
            self._exit_stack = AsyncExitStack()

        async def patched_disconnect(self: MCPClient) -> None:  # type: ignore[name-defined]
            if not self.connected:
                return
            await _safe_aclose(self._exit_stack)
            self._session = None
            self._initialized = False
            self._exit_stack = AsyncExitStack()

        MCPClient._cleanup_on_error = patched_cleanup_on_error  # type: ignore[assignment]
        MCPClient.disconnect = patched_disconnect  # type: ignore[assignment]

        original_mcp_native_tool_init = MCPNativeTool.__init__

        def patched_mcp_native_tool_init(
            self: MCPNativeTool,
            mcp_client: Any,
            tool_name: str,
            tool_schema: Dict[str, Any],
            server_name: str,
            original_tool_name: str | None = None,
        ) -> None:
            """
            Disable CrewAI's parallel native-tool execution path for MCP tools.

            CrewAI skips parallel native execution when any tool in the batch has
            max_usage_count configured. MCP native tools are not thread-safe under
            parallel execution due to per-call event loop/context teardown.
            """
            original_mcp_native_tool_init(
                self,
                mcp_client,
                tool_name,
                tool_schema,
                server_name,
                original_tool_name=original_tool_name,
            )
            if getattr(self, "max_usage_count", None) is None:
                self.max_usage_count = 1_000_000

        MCPNativeTool.__init__ = patched_mcp_native_tool_init  # type: ignore[assignment]
    except Exception:
        logger.debug("CrewAI MCP runtime patch skipped (import/patch failed)", exc_info=True)

    try:
        from a2a.client.base_client import BaseClient as A2ABaseClient

        original_send_message = A2ABaseClient.send_message

        async def patched_send_message(
            self: A2ABaseClient,
            request: Any,
            *,
            configuration: Any = None,
            context: Any = None,
            request_metadata: Dict[str, Any] | None = None,
            extensions: Any = None,
        ) -> Any:
            resolved_run_id = clean_run_id((request_metadata or {}).get("run_id"))
            if resolved_run_id is None:
                request_message_metadata = getattr(request, "metadata", None)
                if isinstance(request_message_metadata, dict):
                    resolved_run_id = clean_run_id(request_message_metadata.get("run_id"))
            if resolved_run_id is None:
                resolved_run_id = clean_run_id(LANGFUSE_MANAGER.active_run_id)
            if resolved_run_id is None:
                raise ValueError("Missing run_id for A2A BaseClient.send_message request_metadata.")
            merged_metadata = dict(request_metadata or {})
            merged_metadata["run_id"] = resolved_run_id
            async for event in original_send_message(
                self,
                request,
                configuration=configuration,
                context=context,
                request_metadata=merged_metadata,
                extensions=extensions,
            ):
                yield event

        A2ABaseClient.send_message = patched_send_message  # type: ignore[assignment]
    except Exception:
        logger.debug("CrewAI A2A runtime patch skipped (import/patch failed)", exc_info=True)

    _CREWAI_RUNTIME_PATCHED = True


class LangfuseManager:
    """Lightweight Langfuse wrapper with per-run trace contexts."""

    def __init__(self) -> None:
        self.client: Any = None
        self._enabled: bool = False
        self._active_run_id: Optional[str] = None
        self._run_spans: Dict[str, Any] = {}
        self._trace_ids: Dict[str, str] = {}
        self._init_client()

    def _init_client(self) -> None:
        if not PipelineConfig.LANGFUSE_PUBLIC_KEY or not PipelineConfig.LANGFUSE_SECRET_KEY or not PipelineConfig.LANGFUSE_HOST:
            logger.warning("Langfuse not configured; skipping Langfuse initialization.")
            return
        blocked_scopes = [
            # A2A SDK spans (e.g., EventQueue.dequeue_event) that spam traces
            "a2a-python-sdk",
            "a2a.server.events.event_queue",
            "a2a.server.events.in_memory_queue_manager",
            "a2a.server.request_handlers.jsonrpc_handler",
            "a2a.server.request_handlers.default_request_handler",
        ]
        self.client = Langfuse(
            public_key=PipelineConfig.LANGFUSE_PUBLIC_KEY,
            secret_key=PipelineConfig.LANGFUSE_SECRET_KEY,
            host=PipelineConfig.LANGFUSE_HOST,
            blocked_instrumentation_scopes=blocked_scopes,
        )
        try:
            CrewAIInstrumentor().instrument(skip_dep_check=True)
        except Exception:
            logger.debug("CrewAI instrumentation init failed", exc_info=True)
        self._enabled = True
        logger.info("Langfuse client initialized.")

    @property
    def active_run_id(self) -> Optional[str]:
        """Return the currently bound run identifier."""
        return self._active_run_id

    def set_active_run(self, run_id: Optional[str]) -> None:
        """Bind or clear the active run identifier."""
        self._active_run_id = run_id

    def ensure_trace(self, run_id: str, *, name: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
        """
        Create or retrieve a TraceContext for a run and seed a root span so the trace exists.
        """
        if not run_id:
            return None
        if not self._enabled or self.client is None:
            return None
        if run_id in self._trace_ids:
            return {"trace_id": self._trace_ids[run_id]}
        try:
            trace_id = self.client.create_trace_id(seed=run_id)
        except Exception:
            trace_id = hashlib.md5(run_id.encode("utf-8")).hexdigest()
        self._trace_ids[run_id] = trace_id
        try:
            with self.client.start_as_current_observation(
                as_type="span",
                name=name,
                trace_context={"trace_id": trace_id},
                metadata={**(metadata or {}), "run_id": run_id},
                input={"run_id": run_id, **(metadata or {})},
            ):
                pass
        except Exception:
            logger.debug("Langfuse ensure_trace failed", exc_info=True)
        return {"trace_id": trace_id}

    def get_trace_id(self, run_id: str) -> Optional[str]:
        """Return the Langfuse trace id for a run if known."""
        return self._trace_ids.get(run_id)

    def start_run_span(self, run_id: str, *, name: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """Start a long-lived span for the run; caller must end it."""
        if not self._enabled or self.client is None:
            return None
        self.ensure_trace(run_id, name=name, metadata=metadata)
        trace_id = self._trace_ids.get(run_id)
        if trace_id is None:
            return None
        try:
            cm = self.client.start_as_current_observation(
                as_type="span",
                name=name,
                trace_context={"trace_id": trace_id},
                metadata={**(metadata or {}), "run_id": run_id},
            )
            span = cm.__enter__()
            self._run_spans[run_id] = cm
            return span
        except Exception:
            logger.debug("Langfuse start_run_span failed", exc_info=True)
            return None

    def end_run_span(self, run_id: str) -> None:
        """End the run span if present."""
        cm = self._run_spans.pop(run_id, None)
        if cm is None:
            return
        try:
            cm.__exit__(None, None, None)
        except Exception:
            logger.debug("Failed to close Langfuse run span for %s", run_id, exc_info=True)

    def start_span(
        self,
        *,
        run_id: Optional[str],
        name: str,
        input_payload: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        as_type: str = "span",
    ) -> Optional[Tuple[Any, Any]]:
        """Start a child observation and return the context manager."""
        if not self._enabled or self.client is None:
            return None
        trace_id = self._trace_ids.get(run_id or self._active_run_id)
        if trace_id is None:
            return None
        try:
            cm = self.client.start_as_current_observation(
                as_type=as_type,
                name=name,
                trace_context={"trace_id": trace_id},
                input=input_payload,
                metadata=metadata or {},
            )
            span = cm.__enter__()
            return cm, span
        except Exception:
            logger.debug("Langfuse start_span failed", exc_info=True)
            return None

    def end_span(self, handle: Optional[Tuple[Any, Any]], *, output: Optional[Any] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Safely end a previously started observation."""
        if handle is None:
            return
        cm, span = handle
        try:
            if hasattr(span, "update"):
                span.update(output=output, metadata=metadata or {})
        except Exception:
            pass
        try:
            cm.__exit__(None, None, None)
        except Exception:
            logger.debug("Failed to close Langfuse span", exc_info=True)

    def log_event(
        self,
        *,
        run_id: Optional[str],
        name: str,
        input_payload: Optional[Any] = None,
        output_payload: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        as_type: str = "span",
    ) -> None:
        """Record a short-lived observation."""
        handle = self.start_span(
            run_id=run_id,
            name=name,
            input_payload=input_payload,
            metadata=metadata,
            as_type=as_type,
        )
        if handle is None:
            return
        cm, span = handle
        try:
            if hasattr(span, "update"):
                span.update(output=output_payload, metadata=metadata or {})
        except Exception:
            pass
        self.end_span(handle)


LANGFUSE_MANAGER = LangfuseManager()


def _resolve_model_name(model_name: Optional[str]) -> str:
    """
    Resolve and validate the model name, defaulting to OPENAI_MODEL.
    Args:
        model_name: Optional override.
    Returns:
        str: Validated model name.
    Raises:
        ValueError: If the model is not allowed.
    """
    resolved = model_name or PipelineConfig.OPENAI_MODEL
    if resolved not in ALLOWED_MODELS:
        raise ValueError(f"Model '{resolved}' is not allowed. Choose one of: {', '.join(ALLOWED_MODELS)}")
    return resolved


def _build_langfuse_callback(run_id: Optional[str]) -> Optional[Any]:
    """
    Create a Langfuse LangChain callback handler when available.
    """
    trace_id = LANGFUSE_MANAGER.get_trace_id(run_id)
    return LangfuseCallbackHandler(
        public_key=PipelineConfig.LANGFUSE_PUBLIC_KEY,
        update_trace=True,
        trace_context={"trace_id": trace_id}
    )


def set_active_run_id(run_id: Optional[str]) -> None:
    """
    Bind the active run identifier for downstream tracing/logging.
    """
    LANGFUSE_MANAGER.set_active_run(clean_run_id(run_id))


def initialize_run_trace(run_id: str, *, name: str = "pipeline_run", metadata: Optional[Dict[str, Any]] = None) -> None:
    """
    Idempotently create a Langfuse trace for the given run.
    Useful for trigger scripts before dispatching requests.
    """
    LANGFUSE_MANAGER.set_active_run(run_id)
    LANGFUSE_MANAGER.ensure_trace(run_id, name=name, metadata=metadata)


def build_crewai_llm(model_name: Optional[str] = None) -> LLM:
    """
    Create a CrewAI LLM instance with shared settings. Langfuse callbacks are attached per run at execution time.
    Args:
        model_name: Optional override for the OpenAI model.
    Returns:
        LLM: Configured CrewAI LLM.
    """
    _patch_crewai_runtime()
    resolved = _resolve_model_name(model_name)
    llm = LLM(
        model=resolved,
        api_key=PipelineConfig.OPENAI_API_KEY,
        base_url=PipelineConfig.OPENAI_API_BASE,
        timeout=PipelineConfig.OPENAI_REQUEST_TIMEOUT_SECONDS,
    )
    return llm


def build_async_openai_client(run_id: Optional[str] = None) -> Any:
    """
    Build an AsyncOpenAI client using shared settings.
    Args:
        run_id: Optional run identifier to bind for tracing.
    Returns:
        AsyncOpenAI-compatible client.
    """
    set_active_run_id(resolve_run_id(run_id))
    kwargs = {
        "api_key": PipelineConfig.OPENAI_API_KEY,
        "base_url": PipelineConfig.OPENAI_API_BASE,
        "timeout": PipelineConfig.OPENAI_REQUEST_TIMEOUT_SECONDS,
        "max_retries": PipelineConfig.OPENAI_MAX_RETRIES,
    }
    return AsyncOpenAI(**kwargs)


def build_chat_model(model_name: Optional[str] = None, *, run_id: Optional[str] = None) -> OpenAIChatModel:
    """
    Create a Pydantic AI OpenAIChatModel using the shared AsyncOpenAI client.
    Args:
        model_name: Optional override for the OpenAI model.
        run_id: Optional run identifier to bind for tracing/logging.
    Returns:
        OpenAIChatModel: Configured chat model for agents.
    """
    resolved = _resolve_model_name(model_name)
    client = build_async_openai_client(run_id)
    provider = OpenAIProvider(openai_client=client)
    return OpenAIChatModel(
        resolved,
        provider=provider,
    )


def resolve_run_id(run_id: Optional[str]) -> Optional[str]:
    """
    Resolve the run id in priority order: explicit value, then active manager state.
    """
    explicit_run_id = clean_run_id(run_id)
    if explicit_run_id is not None:
        return explicit_run_id
    return clean_run_id(LANGFUSE_MANAGER.active_run_id)


def attach_langfuse_callback(llm: Any, run_id: Optional[str]) -> None:
    """
    Bind a Langfuse callback to an existing CrewAI LLM instance for per-run tracing.
    Args:
        llm: CrewAI LLM instance.
        run_id: Run identifier to associate with the callback.
    """
    resolved_run_id = resolve_run_id(run_id)
    if resolved_run_id is None:
        raise ValueError("Run ID is required to attach Langfuse callback.")
    LANGFUSE_MANAGER.set_active_run(resolved_run_id)
    LANGFUSE_MANAGER.ensure_trace(resolved_run_id, name="simple_run", metadata={"component": "langfuse"})
    callback = _build_langfuse_callback(resolved_run_id)
    if callback is None:
        return
    llm.callbacks = [callback]  # type: ignore[attr-defined]


__all__ = [
    "ALLOWED_MODELS",
    "attach_langfuse_callback",
    "build_async_openai_client",
    "build_chat_model",
    "build_crewai_llm",
    "initialize_run_trace",
    "LANGFUSE_MANAGER",
    "resolve_run_id",
    "set_active_run_id",
]
