"""
End-to-end preparation: cache OSV responses and build scenario SBOMs plus ground truth.
"""
from __future__ import annotations
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any
from evaluation.ansi import colorize_severity_count
from evaluation.models import ScenarioDefinition, SystemOwner, load_scenarios, Severity, SEVERITY_ORDER
from evaluation.osv_client import OSVCache, OSVClient
from evaluation.pool import CandidatePackage, build_candidate_pool, collect_unique_purls, group_by_severity
from evaluation.scenario_builder import ScenarioBuildResult, build_scenario
from evaluation.sbom_dataset import DEFAULT_SBOM_DATASET_RECORD_ID, ensure_sbom_dataset

LOGGER = logging.getLogger(__name__)

def _write_manifest(
    output_root: Path,
    results: List[ScenarioBuildResult],
    config_path: Path,
    cache_dir: Path,
    seed: int,
) -> None:
    """Persist a manifest that indexes all generated scenarios."""
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "cache_dir": str(cache_dir),
        "seed": seed,
        "scenarios": [
            {
                "scenario_id": r.scenario_id,
                "sbom_path": str(r.sbom_path),
                "ground_truth_path": str(r.ground_truth_path),
                "summary": r.summary,
            }
            for r in results
        ],
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

def _load_owner_fixture(path: Path) -> List[Dict[str, Any]]:
    """Load owner fixture list; tolerate single-object file."""
    if not path.exists():
        raise FileNotFoundError(f"Owner fixture not found: {path}")

    raw_owner_data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_owner_data, list):
        # List of objects case
        return [obj for obj in raw_owner_data if isinstance(obj, dict)]
    elif isinstance(raw_owner_data, dict):
        # Single object case
        return [raw_owner_data]
    else:
        raise ValueError("Owner fixture is required and must contain at least one owner entry.")

def _parse_owners(raw_owners: List[Dict[str, Any]]) -> List[SystemOwner]:
    """Convert raw owner data to SystemOwner objects."""
    owners: List[SystemOwner] = []
    for raw in raw_owners:
        owner_id = str(raw.get("owner_id", ""))
        name = str(raw.get("name", ""))
        email = str(raw.get("email", ""))
        # Handle both "Systems" and "systems" keys
        systems = raw.get("Systems") or raw.get("systems") or []
        systems = [str(s) for s in systems if s]

        if owner_id and name and email and systems:
            owners.append(SystemOwner(
                owner_id=owner_id,
                name=name,
                email=email,
                systems=systems
            ))
    return owners

def _collect_owners(
    owners_path: Path | None,
    logger: logging.Logger,
    rng: random.Random,
) -> List[SystemOwner]:
    """Load and parse system owners from fixture if provided and shuffle them."""
    owners: List[SystemOwner] = []
    if owners_path:
        raw_owners = _load_owner_fixture(owners_path)
        owners = _parse_owners(raw_owners)
        logger.info(f"Loaded {len(owners)} owners from {owners_path}")
        rng.shuffle(owners)

        if len(owners) == 0:
            raise ValueError(f"No valid owners found in fixture: {owners_path}")
    return owners

def _derive_scenario_seed(global_seed: int, scenario_index: int, scenario_id: str) -> int:
    """
    Generate a deterministic per-scenario seed derived from the global seed.
    Args:
        global_seed (int): Base seed provided for the run.
        scenario_index (int): Zero-based scenario index.
        scenario_id (str): Scenario identifier string.
    Returns:
        int: Derived seed to initialize a scenario-local RNG.
    """
    material = f"{global_seed}:{scenario_index}:{scenario_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest, 16)

def run_preparation(
    config_path: Path,
    sbom_dataset: Path,
    cache_dir: Path,
    output_root: Path,
    seed: int,
    refresh_cache: bool,
    timeout_s: float,
    retries: int,
    backoff_s: float,
    sbom_download_timeout_s: float,
    sbom_record_id: str = DEFAULT_SBOM_DATASET_RECORD_ID,
    refresh_sbom_dataset: bool = False,
    owners_path: Path | None = None,
    logger: logging.Logger | None = None,
) -> List[ScenarioBuildResult]:
    """
    Execute the preparation flow: cache OSV, build candidates, emit scenarios.
    Args:
        config_path (Path): Scenario config JSON path.
        sbom_dataset (Path): Folder containing sbom.spdx.json subfolders.
        cache_dir (Path): Directory for OSV cache files.
        output_root (Path): Base output directory for scenarios.
        seed (int): Seed for deterministic sampling.
        refresh_cache (bool): Whether to ignore existing cache entries.
        timeout_s (float): HTTP timeout seconds.
        retries (int): Retry attempts for OSV calls.
        backoff_s (float): Backoff base for retries.
        sbom_record_id (str): Zenodo record id used for dataset download.
        sbom_download_timeout_s (float | None): Timeout seconds for dataset download calls; None means no timeout.
        refresh_sbom_dataset (bool): Whether to force dataset re-download from Zenodo.
        owners_path (Path | None): Optional path to system owner fixture.
        logger (logging.Logger | None): Optional logger, defaults to module logger.
    Returns:
        List[ScenarioBuildResult]: Generated scenario metadata.
    """
    active_logger = logger or LOGGER
    rng = random.Random(seed)
    ensure_sbom_dataset(
        dataset_root=sbom_dataset,
        timeout_s=sbom_download_timeout_s,
        record_id=sbom_record_id,
        refresh=refresh_sbom_dataset,
        logger=active_logger,
    )
    scenarios: List[ScenarioDefinition] = load_scenarios(config_path)
    active_logger.info(f"Loaded {len(scenarios)} scenario definitions from {config_path}")
    
    cache = OSVCache(cache_dir, refresh_cache)
    client = OSVClient(cache=cache, timeout_s=timeout_s, retries=retries, backoff_s=backoff_s)
    purls = collect_unique_purls(sbom_dataset)
    active_logger.info(f"Collected {len(purls)} unique purls from dataset {sbom_dataset}")

    candidates: List[CandidatePackage] = build_candidate_pool(purls, client)
    buckets = group_by_severity(candidates)
    clean_count = len(purls) - len(candidates) + len(buckets.get(Severity.NONE, []))
    summary_parts = [colorize_severity_count(sev.label(), len(buckets.get(sev, []))) for sev in SEVERITY_ORDER if sev != Severity.NONE]
    summary_parts.append(colorize_severity_count("clean", clean_count))
    summary = ", ".join(summary_parts)
    active_logger.info(f"Candidate pool: {summary}")
    owners: List[SystemOwner] = _collect_owners(owners_path, active_logger, rng)
    
    results: List[ScenarioBuildResult] = []
    for idx, defn in enumerate(scenarios):
        owner = owners[idx % len(owners)]
        scenario_seed = _derive_scenario_seed(seed, idx, defn.scenario_id)
        scenario_rng = random.Random(scenario_seed)
        res = build_scenario(defn, candidates, scenario_rng, output_root, owner, active_logger)
        results.append(res)

    _write_manifest(output_root, results, config_path, cache_dir, seed)
    active_logger.info(f"Wrote manifest, SBOM and ground truth for {len(results)} scenarios under {output_root}")
    return results
