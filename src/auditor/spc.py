"""
spc.py
======
Statistical Process Control (SPC) module — Phase 7.

Implements the Western Electric (WE) / Nelson rules for detecting
non-random, assignable-cause signals in a process data stream.

Eight Nelson rules are checked. Any violation is a "Stop-the-Line"
signal that should trigger root-cause analysis before the next
Kaizen cycle.

Reference
---------
Nelson, L.S. (1984). "The Shewhart Control Chart — Tests for Special Causes."
Journal of Quality Technology, 16(4), pp. 238–239.

Usage
-----
    from src.auditor.spc import run_spc_analysis

    result = run_spc_analysis(measurements)
    # result["violations"] — list of rule violation dicts
    # result["status"]     — "IN_CONTROL" | "OUT_OF_CONTROL"
"""

from __future__ import annotations

import logging
import os
import json
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SPC_STATE_FILE = "data/spc_state.json"

# ---------------------------------------------------------------------------
# Nelson / Western Electric Rules
# ---------------------------------------------------------------------------

def _rule1(z: np.ndarray) -> list[int]:
    """Rule 1: Any single point beyond ±3σ (Zone A)."""
    return [int(i) for i in np.where(np.abs(z) > 3)[0]]


def _rule2(z: np.ndarray) -> list[int]:
    """Rule 2: 9 (or more) consecutive points on the same side of the centre line."""
    violations = []
    n = len(z)
    for i in range(8, n):
        window = z[i - 8: i + 1]
        if np.all(window > 0) or np.all(window < 0):
            violations.append(i)
    return violations


def _rule3(z: np.ndarray) -> list[int]:
    """Rule 3: 6 (or more) consecutive points steadily increasing or decreasing."""
    violations = []
    n = len(z)
    for i in range(5, n):
        window = z[i - 5: i + 1]
        diffs = np.diff(window)
        if np.all(diffs > 0) or np.all(diffs < 0):
            violations.append(i)
    return violations


def _rule4(z: np.ndarray) -> list[int]:
    """Rule 4: 14 (or more) points alternating up and down."""
    violations = []
    n = len(z)
    for i in range(13, n):
        window = z[i - 13: i + 1]
        diffs = np.diff(window)
        alternating = np.all(diffs[::2] > 0) and np.all(diffs[1::2] < 0) or \
                      np.all(diffs[::2] < 0) and np.all(diffs[1::2] > 0)
        if alternating:
            violations.append(i)
    return violations


def _rule5(z: np.ndarray) -> list[int]:
    """Rule 5: 2 out of 3 consecutive points beyond ±2σ on the same side (Zone B)."""
    violations = []
    n = len(z)
    for i in range(2, n):
        window = z[i - 2: i + 1]
        beyond_pos = np.sum(window > 2)
        beyond_neg = np.sum(window < -2)
        if beyond_pos >= 2 or beyond_neg >= 2:
            violations.append(i)
    return violations


def _rule6(z: np.ndarray) -> list[int]:
    """Rule 6: 4 out of 5 consecutive points beyond ±1σ on the same side (Zone C)."""
    violations = []
    n = len(z)
    for i in range(4, n):
        window = z[i - 4: i + 1]
        beyond_pos = np.sum(window > 1)
        beyond_neg = np.sum(window < -1)
        if beyond_pos >= 4 or beyond_neg >= 4:
            violations.append(i)
    return violations


def _rule7(z: np.ndarray) -> list[int]:
    """Rule 7: 15 or more consecutive points within ±1σ (hugging the centre — stratification)."""
    violations = []
    n = len(z)
    for i in range(14, n):
        window = z[i - 14: i + 1]
        if np.all(np.abs(window) < 1):
            violations.append(i)
    return violations


def _rule8(z: np.ndarray) -> list[int]:
    """Rule 8: 8 or more consecutive points on both sides of centre, none within ±1σ (mixture)."""
    violations = []
    n = len(z)
    for i in range(7, n):
        window = z[i - 7: i + 1]
        if np.all(np.abs(window) > 1):
            violations.append(i)
    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

RULES = [
    (1, "Single point beyond ±3σ",                            _rule1),
    (2, "9 consecutive points same side of centre",           _rule2),
    (3, "6 consecutive points trending monotonically",        _rule3),
    (4, "14 consecutive points alternating up/down",          _rule4),
    (5, "2/3 consecutive points beyond ±2σ (same side)",     _rule5),
    (6, "4/5 consecutive points beyond ±1σ (same side)",     _rule6),
    (7, "15 consecutive points within ±1σ (stratification)", _rule7),
    (8, "8 consecutive points outside ±1σ on both sides",    _rule8),
]


def run_spc_analysis(
    data: np.ndarray,
    mean: Optional[float] = None,
    std: Optional[float] = None,
    rules_to_apply: Optional[list[int]] = None,
    state_file: str = SPC_STATE_FILE,
) -> dict:
    """
    Run full Nelson/Western-Electric SPC rule set on *data*.

    Parameters
    ----------
    data : np.ndarray
        Raw process measurements in chronological order.
    mean : float, optional
        Known process mean (μ). Defaults to sample mean.
    std : float, optional
        Known process std-dev (σ). Defaults to sample std (ddof=1).
    rules_to_apply : list[int], optional
        Subset of rule numbers 1–8 to apply. Defaults to all eight.
    state_file : str
        Path to persist the SPC result JSON.

    Returns
    -------
    dict
        Keys:
        - ``status``       : ``"IN_CONTROL"`` | ``"OUT_OF_CONTROL"``
        - ``violations``   : list of dicts with keys ``rule``, ``description``,
                             ``violation_indices``, ``count``
        - ``rules_checked``: int
        - ``total_violations``: int
        - ``mean``         : float (used centre line)
        - ``std``          : float (used σ)
        - ``n``            : int (number of data points)
        - ``timestamp``    : ISO-8601 UTC string
    """
    data = np.asarray(data, dtype=float)
    if data.size == 0:
        raise ValueError("data array is empty.")
    if data.size < 2:
        raise ValueError("SPC analysis requires at least 2 data points.")

    mu  = float(mean) if mean is not None else float(np.mean(data))
    sig = float(std)  if std  is not None else float(np.std(data, ddof=1))

    if sig == 0:
        logger.warning("SPC: std=0 — all points are identical. Returning IN_CONTROL with no violations.")
        sig = 1e-9  # avoid division by zero

    # Standardise
    z = (data - mu) / sig

    active_rules = rules_to_apply or [r[0] for r in RULES]
    violations: list[dict] = []

    for rule_num, description, fn in RULES:
        if rule_num not in active_rules:
            continue
        indices = fn(z)
        if indices:
            violations.append({
                "rule":              rule_num,
                "description":       description,
                "violation_indices": indices,
                "count":             len(indices),
            })
            logger.warning(
                "SPC Rule %d violated (%d point%s): %s",
                rule_num, len(indices), "s" if len(indices) > 1 else "",
                description,
            )

    status = "OUT_OF_CONTROL" if violations else "IN_CONTROL"
    total_violations = sum(v["count"] for v in violations)

    result = {
        "status":            status,
        "violations":        violations,
        "rules_checked":     len(active_rules),
        "total_violations":  total_violations,
        "mean":              mu,
        "std":               sig,
        "n":                 int(data.size),
        "timestamp":         datetime.utcnow().isoformat() + "Z",
    }

    if status == "IN_CONTROL":
        logger.info("SPC: IN_CONTROL — 0 violations across %d rules.", len(active_rules))
    else:
        logger.warning(
            "SPC: OUT_OF_CONTROL — %d violation event(s) across %d rule(s).",
            total_violations, len(violations),
        )

    # Persist state
    os.makedirs(os.path.dirname(state_file) if os.path.dirname(state_file) else ".", exist_ok=True)
    # Serialise violation_indices as plain lists for JSON
    serialisable = {
        **result,
        "violations": [
            {**v, "violation_indices": list(v["violation_indices"])}
            for v in violations
        ],
    }
    with open(state_file, "w") as fh:
        json.dump(serialisable, fh, indent=2)

    return result


def spc_summary(result: dict) -> str:
    """Return a human-readable SPC summary string."""
    lines = [
        f"SPC Status    : {result['status']}",
        f"Data Points   : {result['n']}",
        f"Centre Line   : μ={result['mean']:.4f}, σ={result['std']:.4f}",
        f"Rules Checked : {result['rules_checked']}",
        f"Total Events  : {result['total_violations']}",
    ]
    if result["violations"]:
        lines.append("Violations:")
        for v in result["violations"]:
            lines.append(f"  Rule {v['rule']:1d} [{v['count']:3d} pts]: {v['description']}")
    return "\n".join(lines)
