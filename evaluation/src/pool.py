"""
Candidate package extraction and GHSA-centric classification powered by OSV.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple
from cvss import CVSS3
from packaging.version import InvalidVersion, Version
from tqdm import tqdm
from evaluation.ansi import colorize_severity_label
from evaluation.models import CandidatePackage, Severity
from evaluation.osv_client import OSVClient

LOGGER = logging.getLogger(__name__)

def _unique_ordered(values: Iterable[str]) -> List[str]:
    """Return items de-duplicated while preserving order."""
    seen: Set[str] = set()
    unique: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique

def _read_spdx(path: Path) -> Dict[str, object]:
    """Load SPDX JSON from disk."""
    return json.loads(path.read_text(encoding="utf-8"))

def _extract_version(purl: str) -> str | None:
    """Return version component from a purl if present."""
    return purl.split("@", 1)[1].strip() if "@" in purl else None

def _extract_purls(sbom: Dict[str, object]) -> List[str]:
    """Collect purls from SPDX externalRefs."""
    purls: List[str] = []
    for pkg in sbom.get("packages", []):
        if not isinstance(pkg, dict):
            continue
        for ext in pkg.get("externalRefs", []):
            if not isinstance(ext, dict):
                continue
            ref_type = str(ext.get("referenceType") or "").lower()
            if ref_type != "purl":
                continue
            locator = ext.get("referenceLocator")
            if locator:
                purls.append(str(locator))
    return _unique_ordered(purls)

def _collect_ghsa_ids(vuln: Dict[str, object]) -> List[str]:
    """Collect GHSA identifiers from id and aliases."""
    ids: List[str] = []
    vulnerability_id = str(vuln.get("id") or "").strip().upper()
    if vulnerability_id.startswith("GHSA-"):
        ids.append(vulnerability_id)
    for alias in vuln.get("aliases", []):
        alias_str = str(alias or "").strip().upper()
        if alias_str.startswith("GHSA-") and alias_str not in ids:
            ids.append(alias_str)
    return ids

def _collect_cve_ids(vuln: Dict[str, object]) -> List[str]:
    """Collect CVE identifiers from aliases."""
    cves: List[str] = []
    for alias in vuln.get("aliases", []):
        alias_str = str(alias or "").strip().upper()
        if alias_str.startswith("CVE-") and alias_str not in cves:
            cves.append(alias_str)
    return cves

def _cvss_score_from_vector(vector: str) -> float | None:
    """Compute CVSS v3.x base score using the cvss library."""
    cleaned = vector.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    if not cleaned.upper().startswith("CVSS:3."):
        return None
    try:
        return float(CVSS3(cleaned).base_score)
    except ValueError:
        return None

def _max_cvss_score(vuln: Dict[str, object]) -> Tuple[float, str | None]:
    """Return the highest CVSS score and the vector that yielded it."""
    best_score = 0.0
    best_vector: str | None = None
    for severity_entry in vuln.get("severity", []):
        if not isinstance(severity_entry, dict):
            continue
        score_raw: Any = severity_entry.get("score")
        try:
            numeric_score = float(score_raw)
        except (TypeError, ValueError):
            vector = str(score_raw or "")
            if vector.upper().startswith("CVSS:3."):
                parsed = _cvss_score_from_vector(vector)
                if parsed is not None and parsed > best_score:
                    best_score = parsed
                    best_vector = vector
            continue
        if numeric_score > best_score:
            best_score = numeric_score
            best_vector = None
    return best_score, best_vector

def _extract_ranges(vuln: Dict[str, object]) -> List[Tuple[str | None, str | None]]:
    """Return all (introduced, fixed/last_affected) ranges for a vuln."""
    ranges: List[Tuple[str | None, str | None]] = []
    for affected in vuln.get("affected", []):
        if not isinstance(affected, dict):
            continue
        for rng in affected.get("ranges", []):
            if not isinstance(rng, dict):
                continue
            introduced = None
            fixed_or_last = None
            for event in rng.get("events", []):
                if not isinstance(event, dict):
                    continue
                if "introduced" in event and event["introduced"] not in (None, "0"):
                    introduced = str(event["introduced"])
                if "fixed" in event and event["fixed"] is not None:
                    fixed_or_last = str(event["fixed"])
                if "last_affected" in event and event["last_affected"] is not None:
                    fixed_or_last = str(event["last_affected"])
            ranges.append((introduced, fixed_or_last))
    return ranges

def _version_in_range(version: str | None, introduced: str | None, fixed_or_last: str | None) -> bool:
    """Check whether version lies within [introduced, fixed_or_last] (inclusive/exclusive on fixed)."""
    if version is None:
        return False
    try:
        v_cur = Version(version)
    except InvalidVersion:
        return False
    if introduced:
        try:
            if v_cur < Version(introduced):
                return False
        except InvalidVersion:
            return False
    if fixed_or_last:
        try:
            # fixed is exclusive; last_affected is inclusive, OSV does not differentiate here, treat as exclusive upper bound.
            if v_cur > Version(fixed_or_last):
                return False
        except InvalidVersion:
            return False
    return True

def classify_package(purl: str, osv_record: Dict[str, object]) -> CandidatePackage:
    """Convert a single OSV response into a GHSA-centric candidate."""
    ghsa_ids: List[str] = []
    ghsa_severities: Dict[str, Severity] = {}
    cve_ids: List[str] = []
    range_start: str | None = None
    range_end: str | None = None
    best_match_score = -1.0
    best_match_severity = Severity.NONE
    best_match_range: Tuple[str | None, str | None] = (None, None)
    best_match_vector: str | None = None
    max_score = 0.0
    max_vector: str | None = None
    max_severity = Severity.NONE
    version = _extract_version(purl)

    for vuln in osv_record.get("vulns", []):
        if not isinstance(vuln, dict):
            continue

        vuln_ghsas = _collect_ghsa_ids(vuln)
        if len(vuln_ghsas) == 0:
            # Skip empty GHSA entries
            continue

        ghsa_ids.extend([gid for gid in vuln_ghsas if gid not in ghsa_ids])
        cve_ids.extend([cid for cid in _collect_cve_ids(vuln) if cid not in cve_ids])
        score, vector = _max_cvss_score(vuln)
        severity = Severity.from_score(score)
        for gid in vuln_ghsas:
            existing = ghsa_severities.get(gid)
            if existing is None or severity > existing:
                ghsa_severities[gid] = severity
        ranges = _extract_ranges(vuln)
        for intro, fixed in ranges:
            if _version_in_range(version, intro, fixed):
                if severity > best_match_severity or (severity == best_match_severity and score > best_match_score):
                    best_match_severity = severity
                    best_match_score = score
                    best_match_range = (intro, fixed)
                    best_match_vector = vector
                break
        if severity > max_severity or (severity == max_severity and score > max_score):
            max_severity = severity
            max_score = score
            max_vector = vector
            # fallback range in case we do not find a version-specific match
            fallback_ranges = ranges or [(None, None)]
            range_start, range_end = fallback_ranges[0]

    if best_match_score >= 0.0:
        max_severity = best_match_severity
        max_score = best_match_score
        max_vector = best_match_vector
        range_start, range_end = best_match_range
    return CandidatePackage(
        purl=purl,
        version=_extract_version(purl),
        ghsa_ids=ghsa_ids,
        ghsa_severities=ghsa_severities,
        cve_ids=cve_ids,
        max_cvss=max_score,
        max_vector=max_vector,
        max_severity=max_severity,
        vuln_range_start=range_start,
        vuln_range_end=range_end,
    )

def collect_unique_purls(dataset_root: Path) -> List[str]:
    """
    Scan the SBOM dataset and return a de-duplicated list of purls.
    Raises:
        FileNotFoundError: If dataset folder is missing.
        ValueError: If no purls are discovered.
    """
    if not dataset_root.exists():
        raise FileNotFoundError(f"SBOM dataset directory not found: {dataset_root}")
    purls: List[str] = []
    for sbom_folder in sorted(dataset_root.iterdir()):
        if not sbom_folder.is_dir():
            continue
        sbom_path = sbom_folder / "sbom.spdx.json"
        if not sbom_path.exists():
            raise FileNotFoundError(f"Missing sbom.spdx.json in {sbom_folder}")
        sbom_doc = _read_spdx(sbom_path)
        purls.extend(_extract_purls(sbom_doc))
    unique_purls = _unique_ordered(purls)
    if len(unique_purls) == 0:
        raise ValueError("No purls could be extracted from dataset")
    LOGGER.info(f"Collected {len(unique_purls)} unique purls")
    return unique_purls

def build_candidate_pool(purls: List[str], client: OSVClient) -> List[CandidatePackage]:
    """Query OSV for each purl (cache-aware) and build the candidate pool."""
    candidates: List[CandidatePackage] = []
    total = len(purls)
    progress = tqdm(purls, desc="Collecting package vulnerabilities", unit="pkg")
    for idx, purl in enumerate(progress, start=1):
        osv_record = client.fetch(purl)
        purl_short = f"{purl[:50]}..." if len(purl) >= 50 else f"{purl}{' ' * (53 - len(purl))}"
        if len(osv_record) == 0:
            candidate = CandidatePackage(
                purl=purl,
                version=_extract_version(purl),
                ghsa_ids=[],
                ghsa_severities={},
                cve_ids=[],
                max_cvss=0.0,
                max_vector=None,
                max_severity=Severity.NONE,
                vuln_range_start=None,
                vuln_range_end=None,
            )
            candidates.append(candidate)
            severity_label = "clean"
            LOGGER.debug(f"[{idx}/{total}] - [{colorize_severity_label(severity_label)}] - purl={purl_short}")
        else:
            candidate = classify_package(purl, osv_record)
            candidates.append(candidate)
            severity_label = candidate.max_severity.label()
            LOGGER.debug(f"[{idx}/{total}] - [{colorize_severity_label(severity_label)}] - purl={purl_short}")
    progress.close()
    return candidates

def group_by_severity(candidates: List[CandidatePackage]) -> Dict[Severity, List[CandidatePackage]]:
    """Group candidates by their max severity."""
    buckets: Dict[Severity, List[CandidatePackage]] = {sev: [] for sev in Severity}
    for candidate in candidates:
        buckets[candidate.max_severity].append(candidate)
    return buckets
