"""
Aggregate evaluation run metrics and fetch Langfuse token usage.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import httpx
from shared.config import PipelineConfig
from shared.model_loader import LANGFUSE_MANAGER

LOGGER = logging.getLogger(__name__)


class LangfuseConfigError(RuntimeError):
    """Raised when Langfuse credentials are missing."""


@dataclass(frozen=True)
class TraceTokenUsage:
    """Token usage for a single Langfuse trace."""
    trace_id: str
    input_tokens: int
    output_tokens: int


class LangfuseTokenFetcher:
    """HTTP client wrapper for Langfuse token usage lookups."""

    def __init__(self, *, timeout_s: float = 10.0, logger: logging.Logger | None = None) -> None:
        self.timeout_s = timeout_s
        self.logger = logger or LOGGER

    def fetch(self, trace_id: str) -> TraceTokenUsage:
        """Fetch token usage for a trace and return input/output counts."""
        host = PipelineConfig.LANGFUSE_HOST
        public_key = PipelineConfig.LANGFUSE_PUBLIC_KEY
        secret_key = PipelineConfig.LANGFUSE_SECRET_KEY
        if not host or not public_key or not secret_key:
            raise LangfuseConfigError("Langfuse host or credentials are not configured.")
        url = f"{host.rstrip('/')}/api/public/traces/{trace_id}"
        try:
            with httpx.Client(auth=(public_key, secret_key), timeout=self.timeout_s) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            self.logger.error(f"Langfuse trace '{trace_id}' request failed!", exc_info=True)
            raise
        usage = self._extract_usage(payload)
        return TraceTokenUsage(trace_id=trace_id, input_tokens=usage[0], output_tokens=usage[1])

    @staticmethod
    def _extract_usage(payload: Dict[str, object]) -> Tuple[int, int]:
        """
        Extract input and output token counts from a Langfuse trace payload.
        Primary path: top-level usage dict or observation-level usage entries.
        """
        tokens_in = 0
        tokens_out = 0
        if not isinstance(payload, dict):
            return tokens_in, tokens_out
        usage_raw = payload.get("usage")
        if isinstance(usage_raw, dict):
            tokens_in += LangfuseTokenFetcher._extract_count(usage_raw, ["input", "input_tokens", "prompt_tokens", "total_input_tokens"])
            tokens_out += LangfuseTokenFetcher._extract_count(usage_raw, ["output", "output_tokens", "completion_tokens", "total_output_tokens"])
        observations = payload.get("observations")
        if isinstance(observations, list):
            for obs in observations:
                if not isinstance(obs, dict):
                    continue
                usage = obs.get("usage")
                if not isinstance(usage, dict):
                    continue
                tokens_in += LangfuseTokenFetcher._extract_count(usage, ["input", "input_tokens", "prompt_tokens", "total_input_tokens"])
                tokens_out += LangfuseTokenFetcher._extract_count(usage, ["output", "output_tokens", "completion_tokens", "total_output_tokens"])
        return tokens_in, tokens_out

    @staticmethod
    def _extract_count(usage: Dict[str, object], keys: List[str]) -> int:
        """Return first integer-like value for any of the provided keys."""
        for key in keys:
            val = usage.get(key)
            if isinstance(val, (int, float)):
                return int(val)
        return 0


def aggregate_run_metrics(
    run_id: str,
    runs_root: Path,
    expected_scenarios: Optional[List[str]] = None,
    *,
    token_fetcher: LangfuseTokenFetcher | None = None,
    logger: logging.Logger | None = None,
) -> Dict[str, object]:
    """
    Compute averaged latency, weighted score, and token usage across scenario outputs.
    Args:
        run_id (str): Shared run identifier matching scenario output filenames.
        runs_root (Path): Root directory containing scenario run outputs.
        expected_scenarios (Optional[List[str]]): Optional list of scenario ids to check coverage.
        token_fetcher (LangfuseTokenFetcher | None): Optional custom token fetcher.
        logger (logging.Logger | None): Optional logger for diagnostics.
    Returns:
        Dict[str, object]: Aggregated metrics and bookkeeping fields.
    """
    eff_logger = logger or LOGGER
    records, source_files = _load_run_records(run_id, runs_root, eff_logger)
    architectures: List[str] = []
    for record in records:
        value = record.get("architecture") if isinstance(record, dict) else None
        if isinstance(value, str) and value.strip():
            architectures.append(value.strip())
    unique_architectures = sorted(set(architectures))
    architecture = unique_architectures[0] if len(unique_architectures) == 1 else None
    scenario_ids = [str(record.get("scenario_id")) for record in records if record.get("scenario_id")]
    missing = []
    if expected_scenarios:
        missing = [sid for sid in expected_scenarios if sid not in scenario_ids]
    record_count = len(records)
    latency_values = [float(record.get("latency_ms", 0.0)) for record in records]
    latency_avg = sum(latency_values) / record_count if record_count else 0.0
    score_values = []
    for record in records:
        metrics = record.get("score") or {}
        metrics_payload = metrics.get("metrics") if isinstance(metrics, dict) else {}
        weighted_score = metrics_payload.get("weighted_score") if isinstance(metrics_payload, dict) else None
        if isinstance(weighted_score, (int, float)):
            score_values.append(float(weighted_score))
    weighted_avg = sum(score_values) / len(score_values) if score_values else 0.0
    fetcher = token_fetcher or LangfuseTokenFetcher(logger=eff_logger)
    tokens_in: List[int] = []
    tokens_out: List[int] = []
    token_failures: List[str] = []
    for record in records:
        record_run_id = str(record.get("run_id") or run_id)
        trace_id = record.get("trace_id")
        if not trace_id and LANGFUSE_MANAGER:
            trace_id = LANGFUSE_MANAGER.get_trace_id(record_run_id)
        trace_identifier = trace_id or record_run_id
        try:
            usage = fetcher.fetch(trace_identifier)
            tokens_in.append(usage.input_tokens)
            tokens_out.append(usage.output_tokens)
        except LangfuseConfigError:
            token_failures.append(trace_identifier)
            break
        except Exception:
            token_failures.append(trace_identifier)
    tokens_in_avg = sum(tokens_in) / record_count if record_count else 0.0
    tokens_out_avg = sum(tokens_out) / record_count if record_count else 0.0
    first_trace = None
    for record in records:
        candidate = record.get("trace_id")
        if candidate:
            first_trace = candidate
            break
    return {
        "run_id": run_id,
        "architecture": architecture,
        "scenario_count": record_count,
        "latency_ms_avg": latency_avg,
        "weighted_score_avg": weighted_avg,
        "tokens_in_avg": tokens_in_avg,
        "tokens_out_avg": tokens_out_avg,
        "missing_scenarios": missing,
        "token_fetch_failures": token_failures,
        "source_files": source_files,
        "trace_id": first_trace,
    }


def write_run_summary(summary: Dict[str, object], runs_root: Path) -> Path:
    """
    Persist aggregated metrics to a summary file under runs_root/summary.
    Args:
        summary (Dict[str, object]): Aggregated run metrics.
        runs_root (Path): Root directory containing scenario run outputs.
    Returns:
        Path: Path to the written summary JSON.
    """
    summary_dir = runs_root / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    out_path = summary_dir / f"{summary.get('run_id')}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out_path


def _load_run_records(run_id: str, runs_root: Path, logger: logging.Logger) -> Tuple[List[Dict[str, object]], List[str]]:
    """Load per-scenario records matching the run id and return records plus source file paths."""
    records: List[Dict[str, object]] = []
    source_files: List[str] = []
    if not runs_root.exists():
        logger.warning("Runs root %s does not exist", runs_root)
        return records, source_files
    for scenario_dir in runs_root.iterdir():
        if not scenario_dir.is_dir():
            continue
        candidate = scenario_dir / f"{run_id}.json"
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
            record = json.loads(content)
            records.append(record)
            source_files.append(str(candidate))
        except Exception:
            logger.error("Failed to read or parse %s", candidate, exc_info=True)
    return records, source_files
