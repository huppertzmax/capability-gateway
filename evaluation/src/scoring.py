"""
Weighted scoring utilities for notification-style outputs.
"""
from __future__ import annotations
import json
from typing import Dict, List, Tuple

DEFAULT_WEIGHTS: Dict[str, float] = {
    "owner_email": 1.0,
    "owner_name": 1.0,
    "affected_systems": 1.0,
    "package_name": 1.0,
    "package_version": 1.0,
    "ghsa_id": 1.0,
    "vulnerable_range_start": 1.0,
    "vulnerable_range_end": 1.0,
}


def _norm_text(val: object) -> str:
    """Return a trimmed lowercase string."""
    return str(val or "").strip().lower()


def _norm_id(val: object) -> str:
    """Normalize vulnerability identifiers."""
    return str(val or "").strip().upper()


def _norm_systems(val: object) -> Tuple[str, ...]:
    """Normalize systems into a sorted tuple."""
    if isinstance(val, (list, tuple, set)):
        items = [str(x).strip().lower() for x in val if str(x).strip()]
    else:
        items = [str(val).strip().lower()] if val else []
    return tuple(sorted(set(items)))


def _normalized_field(key: str, value: object) -> object:
    """Normalize values per field for stable comparison."""
    if key == "affected_systems":
        return _norm_systems(value)
    if key == "ghsa_id":
        return _norm_id(value)
    return _norm_text(value)


def _match_key(item: Dict[str, object]) -> str:
    """Stable key for matching notifications preferring GHSA id."""
    return "|".join(
        [
            _norm_id(item.get("ghsa_id")),
            _norm_text(item.get("owner_email")),
        ]
    )


def _load_items(payload: object) -> List[Dict[str, object]]:
    """Accept dict or list JSON payload and return list of notification dicts."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def parse_json_output(output_text: str) -> List[Dict[str, object]]:
    """Parse agent output text into a list of notification dicts when possible."""
    try:
        parsed = json.loads(output_text)
    except Exception:
        return []
    return _load_items(parsed)


def score_notifications_weighted(
    gt_items: List[Dict[str, object]],
    pred_items: List[Dict[str, object]],
    weights: Dict[str, float] | None = None,
) -> Dict[str, object]:
    """
    Compute weighted correctness per field plus extra/missing notification counts.
    """
    eff_weights = weights or DEFAULT_WEIGHTS
    weight_total = sum(eff_weights.values())
    gt_map = {_match_key(item): item for item in gt_items}
    pred_map = {_match_key(item): item for item in pred_items}
    matched_keys = gt_map.keys() & pred_map.keys()
    missing = len(gt_map.keys() - pred_map.keys())
    extra = len(pred_map.keys() - gt_map.keys())
    total_possible = len(gt_items) * weight_total
    achieved = 0.0
    field_correct: Dict[str, float] = {k: 0.0 for k in eff_weights}
    for key in matched_keys:
        gt_item = gt_map[key]
        pred_item = pred_map[key]
        for field, weight in eff_weights.items():
            if _normalized_field(field, gt_item.get(field)) == _normalized_field(field, pred_item.get(field)):
                achieved += weight
                field_correct[field] += weight
    field_totals: Dict[str, float] = {k: len(gt_items) * w for k, w in eff_weights.items()}
    field_accuracy: Dict[str, float] = {}
    for name, total in field_totals.items():
        if total > 0.0:
            field_accuracy[name] = field_correct[name] / total
        else:
            field_accuracy[name] = 0.0
    weighted_score = achieved / total_possible if total_possible > 0.0 else 0.0
    return {
        "weighted_score": weighted_score,
        "achieved": achieved,
        "total_possible": total_possible,
        "matched": len(matched_keys),
        "missing": missing,
        "extra": extra,
        "field_accuracy": field_accuracy,
    }
