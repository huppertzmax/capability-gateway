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
    try:
        with fixture_path.open("r", encoding="utf-8") as handle:
            content: Any = json.load(handle)
        return json.dumps(content)
    except FileNotFoundError as exc:
        logger.error("Fixture %s not found: %s", file_name, exc, exc_info=True)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Fixture %s is invalid JSON: %s", file_name, exc, exc_info=True)
        raise
    except OSError as exc:
        logger.error("Failed to load fixture %s: %s", file_name, exc, exc_info=True)
        raise
