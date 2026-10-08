"""
Evaluation run scaffolding: iterate scenarios and trigger the pipeline per scenario.
"""
from __future__ import annotations
import asyncio
import json
import logging
import shutil
import time
import traceback
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from gateway.gateway.clients.a2a.a2a_client import A2AClient, AgentConfig
from shared.model_loader import initialize_run_trace, LANGFUSE_MANAGER
from evaluation.scoring import score_notifications_weighted
from evaluation.run_summary import aggregate_run_metrics, write_run_summary

LOGGER = logging.getLogger(__name__)
NOTIFICATIONS_DIR = Path("evaluation/data/tmp/notifications")


def _build_run_id() -> str:
    """Build a stable run identifier with timestamp prefix."""
    timestamp = datetime.now().strftime("%d-%m-%y_%H-%M")
    return f"{timestamp}_ID-{uuid4()}"

def _load_manifest(manifest_path: Path) -> List[Dict[str, object]]:
    """Read manifest file and return scenario entries."""
    raw_text = manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    scenarios = data.get("scenarios", []) if isinstance(data, dict) else []
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError(f"Manifest {manifest_path} does not contain scenarios")
    return [s for s in scenarios if isinstance(s, dict)]

def _select_payload(ground_truth_path: Path) -> Tuple[List[str], str]:
    """Pick the GHSA payload from the ground truth GHSA list."""
    data = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    ghsa_ids = data.get("ghsa_ids")
    normalized_ids = [gid.strip() for gid in ghsa_ids if isinstance(gid, str) and gid.strip()]
    joined_ids = ', '.join(normalized_ids)
    payload = f"Analyze GHSA IDs: {joined_ids}"
    return joined_ids, payload


def _flatten_notifications(raw: object) -> List[Dict[str, object]]:
    """Return a flat list of notification dicts from nested lists/dicts."""
    result: List[Dict[str, object]] = []
    stack: List[object] = [raw]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, list):
            stack.extend(item)
    return result

async def _trigger_agent(agent_url: str, skill: str, payload: str, run_id: str) -> str:
    """Execute one agent call and return the output text."""
    initialize_run_trace(run_id, name="evaluation", metadata={"skill": skill})
    client = A2AClient(AgentConfig("evaluation", agent_url))
    await client.connect()
    LOGGER.info(f"Executing skill {skill} with payload '{payload}'")
    output = await client.execute_skill(skill, payload, run_id=run_id)
    await client.disconnect()
    return str(output)

class ScenarioRunner:
    """Execute scenarios from manifest and persist run outputs."""
    def __init__(
        self,
        agent_url: str,
        skill: str,
        run_output_dir: Path,
        run_id: str,
        architecture: str | None,
        logger: logging.Logger | None,
    ):
        """Store shared execution parameters."""
        self.agent_url = agent_url
        self.skill = skill
        self.run_output_dir = run_output_dir
        self.run_id = run_id
        self.architecture = architecture
        self.logger = logger or LOGGER

    def run_entry(self, entry: Dict[str, object]) -> Dict[str, object]:
        """Run one scenario entry and return the recorded metadata."""
        scenario_id = str(entry.get("scenario_id"))
        ground_truth_path = Path(entry.get("ground_truth_path"))
        sbom_path = Path(entry.get("sbom_path"))
        joined_ids, payload = _select_payload(ground_truth_path)
        run_id = f"{scenario_id}|{self.run_id}"
        # Copy SBOM to MCP default fixture location for this run
        mcp_dir = Path("mcp-server")
        mcp_dir.mkdir(parents=True, exist_ok=True)
        target = mcp_dir / "sbom.json"
        target.write_text(sbom_path.read_text(encoding="utf-8"), encoding="utf-8")
        LANGFUSE_MANAGER.log_event(
            run_id=run_id, 
            name="eval.trigger.start", 
            input_payload={
                "scenario_id": scenario_id, 
                "ghsa_ids": joined_ids
            }
        )
        t0 = time.perf_counter()
        _ = asyncio.run(_trigger_agent(self.agent_url, self.skill, payload, run_id))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        LANGFUSE_MANAGER.log_event(
            run_id=run_id, 
            name="eval.trigger.end", 
            output_payload={"latency_ms": latency_ms}
        )
        score, expected_notifications, predicted_notifications = self._score_output(ground_truth_path, run_id)
        LANGFUSE_MANAGER.log_event(
            run_id=run_id, 
            name="eval.scoring", 
            input_payload={"scenario_id": scenario_id}, 
            output_payload=score
        )
        trace_id = LANGFUSE_MANAGER.get_trace_id(run_id)
        record = {
            "scenario_id": scenario_id,
            "run_id": run_id,
            "architecture": self.architecture,
            "trace_id": trace_id,
            "ghsa_ids": joined_ids,
            "payload": payload,
            "agent_url": self.agent_url,
            "skill": self.skill,
            "sbom_path": str(sbom_path),
            "ground_truth_path": str(ground_truth_path),
            "latency_ms": latency_ms,
            "score": score,
            "notifications_expected": expected_notifications,
            "notifications_predicted": predicted_notifications,
        }
        out_path = self._write_record(scenario_id, run_id, record)
        self.logger.info(f"Scenario {scenario_id}: recorded run at {out_path}")
        return record

    def _score_output(self, ground_truth_path: Path, run_id: str) -> Tuple[Dict[str, object], List[Dict[str, object]], List[Dict[str, object]]]:
        """
        Compare persisted notification file to ground truth notifications.
        Args:
            ground_truth_path (Path): Path to ground truth JSON.
            run_id (str): Run identifier matching the notification filename.
        Returns:
            Tuple[Dict[str, object], List[Dict[str, object]], List[Dict[str, object]]]: Score summary, expected notifications, predicted notifications.
        """
        try:
            gt = json.loads(ground_truth_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "error", "reason": "ground_truth_read_failed"}, [], []
        gt_items = gt.get("notifications") if isinstance(gt, dict) else None
        if not isinstance(gt_items, list):
            return {"status": "error", "reason": "notifications_missing"}, [], []
        notification_path = NOTIFICATIONS_DIR / f"{run_id}.json"
        try:
            pred_raw = json.loads(notification_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"status": "fail", "reason": "notification_file_missing"}, gt_items, []
        except Exception:
            return {"status": "fail", "reason": "notification_file_unreadable"}, gt_items, []
        pred_items = _flatten_notifications(pred_raw)
        metrics = score_notifications_weighted(gt_items, pred_items)
        status = "pass" if metrics.get("missing") == 0 and metrics.get("extra") == 0 and metrics.get("weighted_score", 0.0) >= 1.0 else "partial"
        return {"status": status, "metrics": metrics}, gt_items, pred_items

    def _write_record(self, scenario_id: str, run_id: str, record: Dict[str, object]) -> Path:
        """Write run record to disk and return the path."""
        run_dir = self.run_output_dir / scenario_id
        run_dir.mkdir(parents=True, exist_ok=True)
        # Extract the second part of run_id if it contains |
        file_run_id = run_id.split("|")[1] if "|" in run_id else run_id
        out_path = run_dir / f"{file_run_id}.json"
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return out_path

def run_evaluation(
    manifest_path: Path,
    scenario_id: Optional[str],
    agent_url: str,
    skill: str,
    run_output_dir: Path,
    run_id: Optional[str] = None,
    *,
    architecture: Optional[str] = None,
    logger: logging.Logger | None = None,
) -> List[Dict[str, object]]:
    """
    Iterate scenarios from manifest and trigger the pipeline once per scenario.
    Args:
        manifest_path (Path): Path to manifest.json produced during preparation.
        scenario_id (Optional[str]): Optional filter for a single scenario.
        agent_url (str): Target agent URL.
        skill (str): Skill name to execute.
        run_output_dir (Path): Directory to store run metadata.
        run_id (Optional[str]): Optional shared run identifier used across scenarios.
        architecture (Optional[str]): Optional pipeline architecture label for result metadata.
        logger (logging.Logger): Logger for progress messages.
    Returns:
        List[Dict[str, object]]: Run records for each scenario.
    """
    shared_run_id = run_id or _build_run_id()
    LANGFUSE_MANAGER.set_active_run(shared_run_id)
    scenarios = _load_manifest(manifest_path)
    if scenario_id:
        scenarios = [s for s in scenarios if s.get("scenario_id") == scenario_id]
    if not scenarios:
        raise ValueError(f"No scenarios to run in manifest {manifest_path} for filter {scenario_id}")
    _clear_notifications_dir()
    if logger:
        logger.info("Starting evaluation run %s for %s scenarios", shared_run_id, len(scenarios))
    records: List[Dict[str, object]] = []
    runner = ScenarioRunner(agent_url, skill, run_output_dir, shared_run_id, architecture, logger)
    for entry in scenarios:
        try:
            records.append(runner.run_entry(entry))
        except Exception as exc:
            failed_scenario_id = str(entry.get("scenario_id") or "")
            failed_run_id = f"{failed_scenario_id}|{shared_run_id}" if failed_scenario_id else shared_run_id
            record: Dict[str, object] = {
                "scenario_id": failed_scenario_id,
                "run_id": failed_run_id,
                "architecture": architecture,
                "agent_url": agent_url,
                "skill": skill,
                "score": {"status": "error", "reason": "scenario_exception"},
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            try:
                run_dir = run_output_dir / (failed_scenario_id or "unknown")
                run_dir.mkdir(parents=True, exist_ok=True)
                out_path = run_dir / f"{shared_run_id}.json"
                out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
                if logger:
                    logger.error("Scenario %s failed; wrote error record at %s", failed_scenario_id, out_path, exc_info=True)
            except Exception:
                if logger:
                    logger.error("Scenario %s failed; also failed to write error record", failed_scenario_id, exc_info=True)
            records.append(record)
    try:
        expected = [str(item.get("scenario_id")) for item in scenarios if item.get("scenario_id")]
        summary = aggregate_run_metrics(shared_run_id, run_output_dir, expected_scenarios=expected, logger=logger)
        summary_path = write_run_summary(summary, run_output_dir)
        if logger:
            logger.info("Aggregated run %s summary written to %s", shared_run_id, summary_path)
    except OSError:
        if logger:
            logger.error("Failed to aggregate run metrics for %s", shared_run_id, exc_info=True)
        raise
    return records


def _clear_notifications_dir() -> None:
    """Remove previous notification files before running evaluation."""
    shutil.rmtree(NOTIFICATIONS_DIR, ignore_errors=True)
    NOTIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
