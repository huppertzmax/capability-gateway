"""
Utilities for loading agent prompt definitions from JSON config files.
"""
import json
from pathlib import Path
from typing import Any, Dict


def load_prompt_config(config_source: Path | Dict[str, Any]) -> Dict[str, Any]:
    """
    Load a prompt configuration from a JSON file or a pre-parsed dict.
    Args:
        config_source (Path | Dict[str, Any]): Path to the JSON config file or an already parsed dictionary.
    Returns:
        Dict[str, Any]: Parsed configuration containing role, goal, backstory, and tasks.
    Raises:
        ValueError: If the file is missing required keys or cannot be read.
    """
    if isinstance(config_source, dict):
        data = config_source
        config_label = 'dict'
    else:
        config_label = str(config_source)
        with config_source.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
    required = ['role', 'goal', 'backstory', 'tasks']
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Missing keys in {config_label}: {', '.join(missing)}")
    return data
