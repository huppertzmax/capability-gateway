"""
Utilities for selecting the pipeline architecture and resolving config paths.
"""
import json
from pathlib import Path

SUPPORTED_ARCHITECTURES = ('crew', 'simple')


def resolve_architecture(override: str | None = None) -> str:
    """
    Resolve the pipeline architecture from an explicit override or environment variable.
    Args:
        override (str | None): Optional explicit architecture value.
    Returns:
        str: Normalized architecture name.
    Raises:
        ValueError: If the architecture is unsupported.
    """
    architecture = (override or 'crew').strip().lower()
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ValueError(f"Unsupported architecture '{architecture}'. Use one of {SUPPORTED_ARCHITECTURES}.")
    return architecture


def load_arch_config(agent_dir: Path, architecture: str, filename: str | None = None) -> dict:
    """
    Load the architecture-specific configuration from a unified config file.
    Args:
        agent_dir (Path): Directory of the current agent.
        architecture (str): Selected architecture ('crew' or 'simple').
        filename (str | None): Optional explicit config filename. Defaults to '<architecture>.json'.
    Returns:
        dict: Configuration for the requested architecture.
    """
    config_filename = filename or f'{architecture}.json'
    config_path = agent_dir / 'config' / config_filename
    with config_path.open('r', encoding='utf-8') as handle:
        config = json.load(handle)
    return config
