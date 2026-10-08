"""
Gateway service package helpers.
"""

from importlib import metadata


def get_version() -> str:
    """
    Return the installed gateway package version.
    Returns:
        str: Version string or default when distribution is missing.
    """
    try:
        return metadata.version("gateway")
    except metadata.PackageNotFoundError:
        return "0.0.0"


__all__ = ["get_version"]
