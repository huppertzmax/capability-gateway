"""
Python entrypoint for preparation and evaluation pipeline execution.
"""
from __future__ import annotations
import argparse
import signal
import subprocess
import sys
import shutil
import logging
from pathlib import Path
from types import FrameType
from typing import Callable, Dict, List, Optional, Sequence
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
LOGS_DIR = PROJECT_ROOT / "logs"
PREP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prep.sh"
EVAL_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "eval.sh"
ARCH_CHOICES = ("simple", "simple-crew", "gateway", "gateway-rag")
SignalHandler = Callable[[int, Optional[FrameType]], None]


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for pipeline delegation."""
    parser = argparse.ArgumentParser(description="Pipeline wrapper delegating to scripts/prep.sh and scripts/eval.sh")
    parser.add_argument("--prep", dest="prep", action="store_true", help="Run preparation mode only.")
    parser.add_argument("--arch", choices=ARCH_CHOICES, help="Run evaluation for the selected architecture.")
    parser.add_argument("--mcp-bloat", action="store_true", help="Enable MCP bloat server during evaluation.")
    parser.add_argument("--mcp-bloat-tools", type=int, help="Set globally exposed MCP bloat tool count.")
    parser.add_argument("--test", action="store_true", help="Use test scenario configuration for preparation mode.")
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Apply argument constraints matching the historical shell wrapper behavior."""
    if args.prep and args.arch:
        parser.error("Cannot use --arch with --prep; choose one mode.")
    if not args.prep and not args.arch:
        parser.error("Must provide --arch when not running --prep.")
    if not args.prep and args.test:
        parser.error("--test is only valid with --prep.")
    if args.prep and args.mcp_bloat:
        parser.error("--mcp-bloat is only valid with --arch.")
    if args.prep and args.mcp_bloat_tools is not None:
        parser.error("--mcp-bloat-tools is only valid with --arch.")
    if args.mcp_bloat_tools is not None and not args.mcp_bloat:
        parser.error("--mcp-bloat-tools requires --mcp-bloat.")
    if args.mcp_bloat_tools is not None and args.mcp_bloat_tools < 0:
        parser.error("--mcp-bloat-tools must be an integer >= 0.")


def _clean_logs_dir(logs_dir: Path) -> None:
    """Delete log directory."""
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
        logger.debug(f"Cleaned logs directory: {logs_dir}")


def _build_delegate_command(args: argparse.Namespace) -> List[str]:
    """Build the delegated shell script command based on validated CLI args."""
    if args.prep:
        command = [str(PREP_SCRIPT_PATH)]
        if args.test:
            command.append("--test")
        return command
    command = [str(EVAL_SCRIPT_PATH), "--arch", str(args.arch)]
    if args.mcp_bloat:
        command.append("--mcp-bloat")
    if args.mcp_bloat_tools is not None:
        command.extend(["--mcp-bloat-tools", str(args.mcp_bloat_tools)])
    return command


def _run_delegate(command: Sequence[str]) -> int:
    """Run delegated script command and forward termination signals."""
    try:
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT))
    except FileNotFoundError:
        logger.error(f"Delegate command not found: {command[0]}", file=sys.stderr)
        return 1
    except OSError as exc:
        logger.error(f"Failed to start delegate command '{' '.join(command)}': {exc}", file=sys.stderr)
        return 1

    def _forward_signal(signum: int, _: Optional[FrameType]) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    previous_handlers: Dict[int, SignalHandler] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handler = signal.getsignal(signum)
        if callable(previous_handler):
            previous_handlers[signum] = previous_handler
        signal.signal(signum, _forward_signal)
    try:
        return process.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    """Parse args, run optional cleanup, and delegate execution to shell scripts."""
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(args, parser)
    if not args.prep:
        _clean_logs_dir(LOGS_DIR)
    command = _build_delegate_command(args)
    return _run_delegate(command)


if __name__ == "__main__":
    raise SystemExit(main())
