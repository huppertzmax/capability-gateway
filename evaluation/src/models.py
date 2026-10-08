"""
Shared models and enums for the new evaluation pipeline.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional

class Severity(IntEnum):
    """CVSS-style severity buckets."""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_score(cls, score: float) -> "Severity":
        """Map a CVSS base score to a severity class."""
        if score <= 0.0:
            return cls.NONE
        if score < 4.0:
            return cls.LOW
        if score < 7.0:
            return cls.MEDIUM
        if score < 9.0:
            return cls.HIGH
        return cls.CRITICAL

    @classmethod
    def from_label(cls, label: str) -> "Severity":
        """Normalize text labels."""
        norm = label.strip().lower()
        mapping = {
            "none": cls.NONE,
            "no": cls.NONE,
            "low": cls.LOW,
            "medium": cls.MEDIUM,
            "med": cls.MEDIUM,
            "moderate": cls.MEDIUM,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
            "crit": cls.CRITICAL,
        }
        return mapping.get(norm, cls.NONE)

    def label(self) -> str:
        """Return lowercase severity name."""
        return self.name.lower()

SEVERITY_ORDER: List[Severity] = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.NONE,
]

@dataclass(frozen=True)
class ScenarioDefinition:
    """Validated scenario specification."""
    scenario_id: str
    num_packages: int
    vulnerable_packages: int
    vulnerable_severity_counts: Dict[Severity, int]

@dataclass(frozen=True)
class CandidatePackage:
    """Normalized package entry with GHSA-focused severity labels."""
    purl: str
    version: Optional[str]
    ghsa_ids: List[str]
    ghsa_severities: Dict[str, Severity]
    cve_ids: List[str]
    max_cvss: float
    max_vector: Optional[str]
    max_severity: Severity
    vuln_range_start: Optional[str]
    vuln_range_end: Optional[str]

@dataclass(frozen=True)
class SystemOwner:
    """System owner information."""
    owner_id: str
    name: str
    email: str
    systems: List[str]

def load_scenarios(config_path: Path) -> List[ScenarioDefinition]:
    """
    Load and validate scenario definitions from JSON.
    Args:
        config_path (Path): Path to JSON list of scenario objects.
    Returns:
        List[ScenarioDefinition]: Validated scenarios.
    Raises:
        FileNotFoundError: If the file is missing.
        ValueError: If structure is invalid.
    """
    data = json.loads(config_path.read_text(encoding="utf-8"))
    scenarios: List[ScenarioDefinition] = []
    for _, item in enumerate(data):
        scenario_id = str(item.get("id") or "").strip()
        num_packages_raw = item.get("num_packages")
        vuln_count_raw = item.get("vulnerable_packages")
        num_packages = int(num_packages_raw)
        vuln_count = int(vuln_count_raw)
        severities_raw = item.get("vulnerable_severity_counts") or {}
        severities: Dict[Severity, int] = {}
        for key, val in severities_raw.items():
            severity = Severity.from_label(str(key))
            count = int(val)
            severities[severity] = count
        total_from_counts = sum(severities.values())
        if total_from_counts != vuln_count:
            raise ValueError(
                f"Scenario {scenario_id} vulnerable_severity_counts sums to {total_from_counts}, expected {vuln_count}",
            )
        scenarios.append(
            ScenarioDefinition(
                scenario_id=scenario_id,
                num_packages=num_packages,
                vulnerable_packages=vuln_count,
                vulnerable_severity_counts=severities,
            )
        )
    return scenarios
