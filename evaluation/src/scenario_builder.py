"""
Scenario construction logic: target calculation, package selection, and file emission.
"""
from __future__ import annotations
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from evaluation.models import CandidatePackage, ScenarioDefinition, Severity, SystemOwner, SEVERITY_ORDER
from evaluation.pool import group_by_severity

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class ScenarioBuildResult:
    """File paths and summary for a generated scenario."""
    scenario_id: str
    sbom_path: Path
    ground_truth_path: Path
    summary: Dict[str, int]

def compute_targets(defn: ScenarioDefinition) -> Dict[Severity, int]:
    """
    Compute exact package targets per severity for a scenario.
    Args:
        defn (ScenarioDefinition): Scenario inputs.
    Returns:
        Dict[Severity, int]: Counts for none/low/medium/high/critical.
    """
    total_vuln = defn.vulnerable_packages
    total_none = defn.num_packages - total_vuln
    counts: Dict[Severity, int] = {sev: 0 for sev in SEVERITY_ORDER}
    for sev, count in defn.vulnerable_severity_counts.items():
        counts[sev] = counts.get(sev, 0) + count
    counts[Severity.NONE] = total_none
    return counts

def _choose_candidates(
    buckets: Dict[Severity, List[CandidatePackage]],
    targets: Dict[Severity, int],
    rng: random.Random,
) -> List[CandidatePackage]:
    """Sample packages without replacement to satisfy severity targets."""
    used: set[str] = set()
    selection: List[CandidatePackage] = []
    inventory = {sev: len(buckets.get(sev, [])) for sev in SEVERITY_ORDER}
    for sev in SEVERITY_ORDER:
        needed = targets.get(sev, 0)
        available = [c for c in buckets.get(sev, []) if c.purl not in used]
        rng.shuffle(available)
        if len(available) < needed:
            inv_summary = ", ".join([f"{name.label()}={count}" for name, count in inventory.items()])
            raise ValueError(f"Not enough {sev.label()} packages: needed {needed}, available {len(available)}; inventory {inv_summary}")
        chosen = available[:needed]
        selection.extend(chosen)
        for cand in chosen:
            used.add(cand.purl)
    return selection

def _purl_to_name(purl: str) -> str:
    """Derive a display name from purl for SPDX package name."""
    if purl.startswith("pkg:maven/"):
        body = purl[len("pkg:maven/") :]
        if "@" in body:
            group_artifact = body.split("@", 1)[0]
            return group_artifact.replace("/", ":")
    return purl

def _choose_system_name(owner: SystemOwner) -> str:
    """Select a system name from the owner's systems."""
    if owner.systems:
        return owner.systems[0]
    raise ValueError(f"No system available for scenario given owner {owner.name}")

def _build_spdx_document(scenario_id: str, system_name: str, packages: List[CandidatePackage]) -> Dict[str, object]:
    """Assemble a minimal SPDX document for the scenario."""
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pkgs: List[Dict[str, object]] = []
    for cand in packages:
        spdx_id = hashlib.sha1(cand.purl.encode("utf-8")).hexdigest()
        pkgs.append(
            {
                "SPDXID": f"SPDXRef-{spdx_id}",
                "name": _purl_to_name(cand.purl),
                "versionInfo": cand.version,
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": cand.purl,
                    }
                ],
            }
        )
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.2",
        "name": system_name,
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"https://example.com/spdx/{scenario_id}",
        "creationInfo": {"creators": ["Tool: evaluation"], "created": created},
        "packages": pkgs,
    }

def _build_notifications(
    system_name: str,
    packages: List[CandidatePackage],
    owner: SystemOwner,
    timestamp: str,
) -> List[Dict[str, object]]:
    """Create notification entries matching the target format."""
    notifications: List[Dict[str, object]] = []
    for cand in packages:
        ghsa_candidates = _select_top_ghsa_ids(cand)
        ghsa_id = ghsa_candidates[0] if ghsa_candidates else None
        cve_id = cand.cve_ids[0] if cand.cve_ids else None
        notifications.append(
            {
                "owner_email": owner.email,
                "owner_name": owner.name,
                "affected_systems": [system_name],
                "package_name": _purl_to_name(cand.purl),
                "package_version": cand.version,
                "cve_id": cve_id,
                "ghsa_id": ghsa_id,
                "vulnerable_range_start": cand.vuln_range_start,
                "vulnerable_range_end": cand.vuln_range_end,
                "timestamp": timestamp,
                "severity": cand.max_severity.label(),
            }
        )
    return notifications

def _select_top_ghsa_ids(candidate: CandidatePackage) -> List[str]:
    """Return a single GHSA ID that carries the package's highest severity."""
    if not candidate.ghsa_ids:
        return []
    if not candidate.ghsa_severities:
        return [candidate.ghsa_ids[0]]
    target_severity = candidate.max_severity
    for gid in candidate.ghsa_ids:
        if candidate.ghsa_severities.get(gid) == target_severity:
            return [gid]
    return [candidate.ghsa_ids[0]]

def _build_ground_truth(
    scenario_id: str,
    system_name: str,
    packages: List[CandidatePackage],
    owner: SystemOwner,
) -> Dict[str, object]:
    """Build ground truth structure including summary counts and notifications."""
    vuln_packages = [
        cand
        for cand in packages
        if cand.max_severity != Severity.NONE and (cand.max_cvss or 0.0) > 0.0
    ]

    summary = {sev.label(): 0 for sev in SEVERITY_ORDER}
    for cand in vuln_packages:
        summary[cand.max_severity.label()] = summary.get(cand.max_severity.label(), 0) + 1
    ghsa_ids: List[str] = []
    seen_ghsa: set[str] = set()
    for sev in SEVERITY_ORDER:
        for cand in vuln_packages:
            if cand.max_severity != sev:
                continue
            top_ids = _select_top_ghsa_ids(cand)
            for ghsa_id in top_ids:
                if ghsa_id in seen_ghsa:
                    continue
                ghsa_ids.append(ghsa_id)
                seen_ghsa.add(ghsa_id)
    # Debug-only: include per-package details in ground truth if deeper inspection is needed.
    # pkg_entries: List[Dict[str, object]] = []
    # for cand in vuln_packages:
    #     pkg_entries.append(
    #         {
    #             "purl": cand.purl,
    #             "version": cand.version,
    #             "ghsa_ids": cand.ghsa_ids,
    #             "cve_ids": cand.cve_ids,
    #             "max_cvss": cand.max_cvss,
    #             "max_severity": cand.max_severity.label(),
    #             "max_cvss_vector": cand.max_vector,
    #             "vulnerable_range_start": cand.vuln_range_start,
    #             "vulnerable_range_end": cand.vuln_range_end,
    #         }
    #     )
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    notifications = _build_notifications(system_name, vuln_packages, owner, timestamp)
    return {
        "scenario_id": scenario_id,
        "system": system_name,
        "ghsa_ids": ghsa_ids,
        "summary": summary,
        "notifications": notifications,
    }

def _write_json(path: Path, obj: Dict[str, object]) -> None:
    """Write JSON with deterministic formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def build_scenario(
    defn: ScenarioDefinition,
    all_candidates: List[CandidatePackage],
    rng: random.Random,
    output_root: Path,
    owner: SystemOwner,
    logger: logging.Logger | None = None,
) -> ScenarioBuildResult:
    """
    Build one scenario: select packages, write SBOM and ground truth, return metadata.
    Args:
        defn (ScenarioDefinition): Scenario inputs.
        all_candidates (List[CandidatePackage]): Classified package pool.
        rng (random.Random): Seeded RNG for deterministic selection.
        output_root (Path): Base output directory.
        owner (Optional[SystemOwner]): Owner assigned to this scenario.
        logger (logging.Logger): Logger for info messages.
    Returns:
        ScenarioBuildResult: Paths and summary.
    """
    active_logger = logger or LOGGER
    targets = compute_targets(defn)
    buckets = group_by_severity(all_candidates)
    selected_packages = _choose_candidates(buckets, targets, rng)
    system_name = _choose_system_name(owner)

    scenario_dir = output_root / defn.scenario_id
    sbom_path = scenario_dir / "sbom.spdx.json"
    gt_path = scenario_dir / "ground_truth.json"
    sbom_doc = _build_spdx_document(defn.scenario_id, system_name, selected_packages)
    _write_json(sbom_path, sbom_doc)

    gt_doc = _build_ground_truth(defn.scenario_id, system_name, selected_packages, owner)
    _write_json(gt_path, gt_doc)

    return ScenarioBuildResult(
        scenario_id=defn.scenario_id,
        sbom_path=sbom_path,
        ground_truth_path=gt_path,
        summary=gt_doc["summary"],  # type: ignore[index]
    )
