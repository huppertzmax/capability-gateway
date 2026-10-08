"""
Shared logging utilities for consistent console and file logging across all pipelines.
"""
from __future__ import annotations
import logging
import re
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_ROOT = PROJECT_ROOT / "logs"
HANDLER_TAG_ATTR = "_shared_logging_tag"
CONSOLE_FORMAT = "%(asctime)s | %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s"


def get_logs_root() -> Path:
    """Return the top-level logs directory and ensure it exists."""
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOGS_ROOT


def sanitize_component_name(component_name: str) -> str:
    """Return a filesystem-safe component name."""
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", component_name.strip())
    if not sanitized:
        raise ValueError("component_name must contain at least one non-whitespace character")
    return sanitized


def get_component_log_path(component_group: str, component_name: str) -> Path:
    """
    Build the default log file path for a component.
    Args:
        component_group: Top-level log group such as 'agents', 'gateway', or 'mcp'.
        component_name: Stable component identifier.
    Returns:
        Path: Absolute path to the component log file under gitlab/logs.
    """
    group_name = sanitize_component_name(component_group)
    file_name = sanitize_component_name(component_name)
    logs_dir = get_logs_root() / group_name
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / f"{file_name}.log"


def configure_component_logger(
    logger_name: str,
    *,
    component_group: str,
    component_name: str,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    console_tag: str = "console",
    file_tag: str = "component-file",
    file_mode: str = "a",
) -> logging.Logger:
    """
    Configure a logger with one console handler and one component file handler.
    Args:
        logger_name: Logger namespace used with logging.getLogger.
        component_group: Top-level logs folder.
        component_name: Component log file name.
        console_level: Severity threshold for stdout logs.
        file_level: Severity threshold for file logs.
        console_tag: Internal handler tag used for idempotent configuration.
        file_tag: Internal handler tag used for idempotent configuration.
        file_mode: File open mode ('a' append, 'w' truncate).
    Returns:
        logging.Logger: Configured logger with disabled propagation.
    """
    logger = logging.getLogger(logger_name)
    logger.propagate = False
    logger.setLevel(min(console_level, file_level))
    upsert_console_handler(
        logger=logger,
        tag=console_tag,
        level=console_level,
    )
    upsert_file_handler(
        logger=logger,
        tag=file_tag,
        log_file=get_component_log_path(component_group, component_name),
        level=file_level,
        mode=file_mode,
    )
    return logger


def upsert_console_handler(
    *,
    logger: logging.Logger,
    tag: str,
    level: int,
    formatter: Optional[logging.Formatter] = None,
) -> None:
    """
    Add or replace a tagged stdout handler on the logger.
    Args:
        logger: Logger instance to modify.
        tag: Stable handler tag.
        level: Minimum level for this handler.
        formatter: Optional formatter override.
    """
    remove_handler_by_tag(logger=logger, tag=tag)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter or logging.Formatter(CONSOLE_FORMAT, datefmt="%H:%M:%S"))
    setattr(console_handler, HANDLER_TAG_ATTR, tag)
    logger.addHandler(console_handler)


def upsert_file_handler(
    *,
    logger: logging.Logger,
    tag: str,
    log_file: Path,
    level: int,
    mode: str = "a",
    formatter: Optional[logging.Formatter] = None,
) -> None:
    """
    Add or replace a tagged file handler on the logger.
    Args:
        logger: Logger instance to modify.
        tag: Stable handler tag.
        log_file: Target log file path.
        level: Minimum level for this handler.
        mode: File open mode ('a' append, 'w' truncate).
        formatter: Optional formatter override.
    """
    remove_handler_by_tag(logger=logger, tag=tag)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode=mode)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter or logging.Formatter(FILE_FORMAT))
    setattr(file_handler, HANDLER_TAG_ATTR, tag)
    logger.addHandler(file_handler)


def remove_handler_by_tag(*, logger: logging.Logger, tag: str) -> None:
    """
    Remove a tagged handler if present.
    Args:
        logger: Logger instance to modify.
        tag: Handler tag to remove.
    """
    for handler in list(logger.handlers):
        if getattr(handler, HANDLER_TAG_ATTR, None) != tag:
            continue
        logger.removeHandler(handler)
        handler.close()


def mirror_handlers_to_root(*, logger: logging.Logger, level: Optional[int] = None) -> None:
    """
    Copy configured handlers from a component logger to the root logger.
    Args:
        logger: Source logger that already has the desired handlers.
        level: Optional root logger level override.
    """
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    for handler in logger.handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(level if level is not None else logger.level)
