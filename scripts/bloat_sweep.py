"""
Run staged MCP bloat sweeps by repeatedly invoking the pipeline entrypoint.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import logging
from pathlib import Path
from typing import List
from shared.logging_utils import configure_component_logger

logger = configure_component_logger(
    logger_name=__name__,
    component_group="run",
    component_name="pipeline.py",
    console_level=logging.WARNING,
    file_level=logging.DEBUG,
    file_mode="a",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCH_CHOICES = ("gateway", "gateway-rag")


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for staged bloat sweep execution."""
    parser = argparse.ArgumentParser(description="Run staged MCP bloat sweeps without metric extraction.")
    parser.add_argument("--start", type=int, required=True, help="Start tool count (inclusive).")
    parser.add_argument("--end", type=int, required=True, help="End tool count (inclusive).")
    parser.add_argument("--step", type=int, default=1, help="Increment step for each sweep iteration.")
    parser.add_argument("--arch", choices=ARCH_CHOICES, action="append", help="Architecture to run (default: gateway & gateway-rag).")
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Validate sweep boundaries and step size."""
    if args.start < 0:
        parser.error("--start must be >= 0.")
    if args.end < 0:
        parser.error("--end must be >= 0.")
    if args.step < 1:
        parser.error("--step must be >= 1.")
    if args.start > args.end:
        parser.error("--start must be <= --end.")


def _resolve_arches(args: argparse.Namespace) -> List[str]:
    """Resolve configured architectures with defaults when omitted."""
    if args.arch:
        return list(args.arch)
    return ["gateway", "gateway-rag"]


def _run_pipeline_once(arch: str, tool_count: int) -> None:
    """Run one pipeline invocation for the provided architecture and bloat tool count."""
    command = [
        sys.executable,
        "-m",
        "scripts.pipeline",
        "--arch",
        arch,
        "--mcp-bloat",
        "--mcp-bloat-tools",
        str(tool_count),
    ]
    logger.info(f"Running arch={arch} with mcp_bloat_tools={tool_count}")
    subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def main() -> int:
    """Run repeated pipeline invocations across the requested bloat tool-count range."""
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)
    arches = _resolve_arches(args)
    for tool_count in range(args.start, args.end + 1, args.step):
        for arch in arches:
            _run_pipeline_once(arch, tool_count)
    logger.info("Bloat sweep completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
