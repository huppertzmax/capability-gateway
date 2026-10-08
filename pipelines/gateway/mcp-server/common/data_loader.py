"""
Utility helpers for loading mock JSON fixtures.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json_fixture(file_name: str) -> str:
    """
    Load a JSON file located next to the server modules.

    Args:
        file_name: File name relative to the mcp-server directory.

    Returns:
        str: Serialized JSON payload.
    """
    fixture_path = Path(__file__).resolve().parent.parent / file_name
    return load_json_from_path(fixture_path)


def load_json_from_path(path: Path) -> str:
    """
    Load JSON content from an absolute path and return serialized string.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            content: Any = json.load(handle)
        return json.dumps(content)
    except Exception as exc:
        logger.error("Exception: %s", exc, exc_info=True)
        raise
