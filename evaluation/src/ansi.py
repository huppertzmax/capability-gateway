"""
ANSI color helpers for evaluation logging.
"""
from __future__ import annotations
from typing import Dict

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_LIGHT_RED = "\033[91m"
ANSI_YELLOW = "\033[33m"
ANSI_GREEN = "\033[32m"
ANSI_BRIGHT_GREEN = "\033[92m"

SEVERITY_COLORS: Dict[str, str] = {
    "critical": ANSI_RED,
    "high": ANSI_LIGHT_RED,
    "medium": ANSI_YELLOW,
    "low": ANSI_GREEN,
    "clean": ANSI_BRIGHT_GREEN,
    "none": ANSI_BRIGHT_GREEN,
}

def colorize_severity_label(label: str) -> str:
    """Return a severity label wrapped in its ANSI color."""
    color = SEVERITY_COLORS.get(label, "")
    if not color:
        return label
    return f"{color}{label}{ANSI_RESET}"

def colorize_severity_count(label: str, value: int) -> str:
    """Return colored severity with appended count."""
    color = SEVERITY_COLORS.get(label, "")
    if not color:
        return f"{label}={value}"
    return f"{color}{label}={value}{ANSI_RESET}"
