"""
Structured logging utilities for the gateway service.
Focuses on capability catalog requests, responses, and execution results.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from shared.logging_utils import get_component_log_path, upsert_console_handler, upsert_file_handler


class GatewayLogger:
    """
    Structured logger for the gateway service with clean console output and verbose file traces.
    """

    def __init__(
        self,
        log_file: Path | None = None,
        *,
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
    ) -> None:
        """
        Create a dedicated logger for the gateway and attach console + file handlers.
        Args:
            log_file: Optional custom path for the gateway log file.
            console_level: Log level for console output.
            file_level: Log level for the log file output.
        """
        self.logger = logging.getLogger("gateway")
        self.logger.propagate = False
        self.logger.setLevel(min(console_level, file_level))
        resolved_log_file = log_file or get_component_log_path("gateway", "gateway")
        upsert_console_handler(
            logger=self.logger,
            tag="gateway-console",
            level=console_level,
            formatter=self._console_formatter(),
        )
        upsert_file_handler(
            logger=self.logger,
            tag="gateway-component-file",
            log_file=resolved_log_file,
            level=file_level,
            mode="a",
            formatter=self._file_formatter(),
        )

    def log_list_capabilities_request(self, caller_agent_name: str) -> float:
        """Log the start of a list capabilities request and return start time."""
        self.logger.info("CAPABILITY → list caller=%s", caller_agent_name)
        self.logger.debug("List capabilities request from caller=%s", caller_agent_name)
        return time.monotonic()

    def log_list_capabilities_response(
        self,
        caller_agent_name: str,
        count: int,
        *,
        start_ts: Optional[float] = None,
    ) -> None:
        """Log the response of a list capabilities request."""
        duration_ms = self._duration_ms(start_ts)
        self.logger.info(
            "CAPABILITY ← list caller=%s count=%s%s",
            caller_agent_name,
            count,
            self._duration_suffix(duration_ms),
        )
        self.logger.debug(
            "List capabilities response caller=%s count=%s duration_ms=%s",
            caller_agent_name,
            count,
            duration_ms,
        )

    def log_get_capability_request(
        self,
        capability_id: str,
        caller_agent_name: str,
    ) -> float:
        """Log the start of a get capability request and return start time."""
        self.logger.info("CAPABILITY → get caller=%s capability_id=%s", caller_agent_name, capability_id)
        self.logger.debug("Get capability request from caller=%s capability_id=%s", caller_agent_name, capability_id)
        return time.monotonic()

    def log_get_capability_response(
        self,
        capability_id: str,
        caller_agent_name: str,
        source_name: str,
        capability_name: str,
        kind: str,
        *,
        start_ts: Optional[float] = None,
    ) -> None:
        """Log the response of a get capability request."""
        duration_ms = self._duration_ms(start_ts)
        self.logger.info(
            "CAPABILITY ← get caller=%s capability_id=%s source=%s kind=%s%s",
            caller_agent_name,
            capability_id,
            source_name,
            kind,
            self._duration_suffix(duration_ms),
        )
        self.logger.debug(
            "Get capability response caller=%s capability_id=%s source=%s capability_name=%s kind=%s duration_ms=%s",
            caller_agent_name,
            capability_id,
            source_name,
            capability_name,
            kind,
            duration_ms,
        )

    def log_execute_capability_request(
        self,
        capability_id: str,
        caller_agent_name: str,
        arguments: Any,
    ) -> float:
        """Log the start of an execute capability request and return start time."""
        self.logger.info("CAPABILITY → execute caller=%s capability_id=%s", caller_agent_name, capability_id)
        self.logger.debug(
            "Execute capability request caller=%s capability_id=%s arguments=%s",
            caller_agent_name,
            capability_id,
            arguments,
        )
        return time.monotonic()

    def log_execute_capability_success(
        self,
        capability_id: str,
        caller_agent_name: str,
        source_name: str,
        capability_name: str,
        kind: str,
        result: Any,
        *,
        start_ts: Optional[float] = None,
    ) -> None:
        """Log successful execution of a capability."""
        duration_ms = self._duration_ms(start_ts)
        self.logger.info(
            "CAPABILITY ✓ execute caller=%s capability_id=%s source=%s kind=%s%s",
            caller_agent_name,
            capability_id,
            source_name,
            kind,
            self._duration_suffix(duration_ms),
        )
        self.logger.debug(
            "Execute capability success caller=%s capability_id=%s source=%s capability_name=%s kind=%s duration_ms=%s result=%s",
            caller_agent_name,
            capability_id,
            source_name,
            capability_name,
            kind,
            duration_ms,
            result,
        )

    def log_execute_capability_error(
        self,
        capability_id: str,
        caller_agent_name: str,
        error: str,
        *,
        start_ts: Optional[float] = None,
    ) -> None:
        """Log failed execution of a capability."""
        duration_ms = self._duration_ms(start_ts)
        self.logger.error(
            "CAPABILITY ✕ execute caller=%s capability_id=%s%s error=%s",
            caller_agent_name,
            capability_id,
            self._duration_suffix(duration_ms),
            error,
        )

    def log_access_denied(
        self,
        capability_id: str,
        caller_agent_name: str,
        reason: str,
    ) -> None:
        """Log when access is denied for a capability."""
        self.logger.warning(
            "CAPABILITY ✕ access denied caller=%s capability_id=%s reason=%s",
            caller_agent_name,
            capability_id,
            reason,
        )

    def log_capability_not_found(
        self,
        capability_id: str,
        caller_agent_name: str,
    ) -> None:
        """Log when a capability is not found."""
        self.logger.warning(
            "CAPABILITY ✕ not found caller=%s capability_id=%s",
            caller_agent_name,
            capability_id,
        )

    def log_exception(self, message: str, *, exc: BaseException) -> None:
        """Log an exception with stack trace."""
        self.logger.error("%s: %s", message, exc, exc_info=True)

    def _console_formatter(self) -> logging.Formatter:
        return logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S")

    def _file_formatter(self) -> logging.Formatter:
        return logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s"
        )

    def _duration_ms(self, start_ts: Optional[float]) -> Optional[float]:
        if start_ts is None:
            return None
        return (time.monotonic() - start_ts) * 1000

    def _duration_suffix(self, duration_ms: Optional[float]) -> str:
        if duration_ms is None:
            return ""
        return f" time={duration_ms:.1f}ms"


def setup_gateway_logging(log_file: Path | None = None) -> GatewayLogger:
    """
    Backwards-compatible helper to build a GatewayLogger.
    Args:
        log_file: Optional path for the gateway's log file.
    Returns:
        GatewayLogger: Configured logger instance.
    """
    return GatewayLogger(log_file)


def get_gateway_logger(log_file: Path | None = None) -> GatewayLogger:
    """Alias for setup_gateway_logging to avoid ambiguity in callers."""
    return GatewayLogger(log_file)


def generate_log_timestamp() -> str:
    """
    Generate a timestamp string for log file naming.
    Format: YYYY-MM-DD_HH-MM-SS
    """
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


__all__ = ["GatewayLogger", "setup_gateway_logging", "get_gateway_logger", "generate_log_timestamp"]
