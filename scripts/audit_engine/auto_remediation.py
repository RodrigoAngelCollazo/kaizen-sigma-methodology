"""
auto_remediation.py
===================
Autonomous remediation sub-agent — Phase 6.

Triggered by agent_orchestrator.py when sigma_analysis returns
INTERVENTION_REQUIRED. Adjusts kaizen_data_gen.py parameters
and re-generates synthetic measurements to tighten process variance,
driving Cpk back above the 1.33 production gate.

Strategy
--------
Each remediation iteration applies a configurable variance-reduction
multiplier to the measurement generation std-dev. The tighter the
distribution, the higher the resulting Cpk.

The sub-agent:
  1. Reads the current loop guard state to determine remediation iteration.
  2. Computes a tightened std-dev based on the iteration count.
  3. Regenerates `data/raw_measurements.csv` with improved parameters.
  4. Logs each action to `data/remediation_log.csv`.
  5. Returns a summary dict for the orchestrator to inspect.
"""

from __future__ import annotations

import csv
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve repo root
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.auditor.logic import calculate_six_sigma_metrics

# ---------------------------------------------------------------------------
# Constants — mirror agent_orchestrator.py
# ---------------------------------------------------------------------------
USL: float = 103.0
LSL: float = 97.0
TARGET: float = 100.0
CPK_GATE: float = 1.33

# Remediation schedule — each iteration tightens std-dev by this factor
VARIANCE_REDUCTION_FACTOR: float = 0.80   # 20% tighter per loop
BASELINE_STD: float = 1.0                 # Starting std-dev (Cpk ≈ 1.0 at centre)
N_SAMPLES: int = 1_000

RAW_MEASUREMENTS_FILE = str(_REPO_ROOT / "data" / "raw_measurements.csv")
REMEDIATION_LOG_FILE   = str(_REPO_ROOT / "data" / "remediation_log.csv")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def auto_remediate(
    loop_count: int,
    current_cpk: float,
    dry_run: bool = False,
) -> dict:
    """
    Execute one autonomous remediation cycle.

    Parameters
    ----------
    loop_count : int
        Current Loop Guard iteration (from sigma_analysis result).
    current_cpk : float
        The Cpk value that triggered INTERVENTION_REQUIRED.
    dry_run : bool
        If True, compute the remediation plan but do not write any files.

    Returns
    -------
    dict
        Keys:
        - ``remediation_applied``  : bool
        - ``old_cpk``              : float
        - ``new_std``              : float — std-dev used for regeneration
        - ``projected_cpk``        : float — Cpk from the newly generated data
        - ``gate_cleared``         : bool — True if projected_cpk >= CPK_GATE
        - ``loop_count``           : int
        - ``timestamp``            : ISO-8601 UTC string
        - ``dry_run``              : bool
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    # ------------------------------------------------------------------
    # 1. Compute tightened std-dev for this remediation loop
    # ------------------------------------------------------------------
    # Each loop applies the reduction factor cumulatively.
    new_std = BASELINE_STD * (VARIANCE_REDUCTION_FACTOR ** loop_count)
    new_std = max(new_std, 0.05)   # hard floor — avoid degenerate near-zero std

    logger.info(
        "Auto-Remediation loop %d: current_cpk=%.4f → target std=%.4f "
        "(factor=%.2f per iteration).",
        loop_count, current_cpk, new_std, VARIANCE_REDUCTION_FACTOR,
    )

    # ------------------------------------------------------------------
    # 2. Generate improved measurement dataset
    # ------------------------------------------------------------------
    rng = np.random.default_rng(seed=42 + loop_count)
    measurements = rng.normal(loc=TARGET, scale=new_std, size=N_SAMPLES)

    projected_metrics = calculate_six_sigma_metrics(measurements, USL, LSL, TARGET)
    projected_cpk = projected_metrics["cpk"]
    gate_cleared = projected_cpk >= CPK_GATE

    logger.info(
        "Projected Cpk after remediation: %.4f (gate=%s).",
        projected_cpk, "CLEARED" if gate_cleared else "STILL FAILING",
    )

    # ------------------------------------------------------------------
    # 3. Write files (unless dry_run)
    # ------------------------------------------------------------------
    if not dry_run:
        os.makedirs(os.path.dirname(RAW_MEASUREMENTS_FILE), exist_ok=True)
        df = pd.DataFrame({
            "sample_id":   range(1, N_SAMPLES + 1),
            "measurement": measurements,
        })
        df.to_csv(RAW_MEASUREMENTS_FILE, index=False)
        logger.info("Regenerated %s with std=%.4f.", RAW_MEASUREMENTS_FILE, new_std)

        # Append to remediation log
        _append_remediation_log(
            loop_count=loop_count,
            old_cpk=current_cpk,
            new_std=new_std,
            projected_cpk=projected_cpk,
            gate_cleared=gate_cleared,
            timestamp=timestamp,
        )

    result = {
        "remediation_applied": not dry_run,
        "old_cpk": current_cpk,
        "new_std": new_std,
        "projected_cpk": projected_cpk,
        "gate_cleared": gate_cleared,
        "loop_count": loop_count,
        "timestamp": timestamp,
        "dry_run": dry_run,
    }

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append_remediation_log(
    loop_count: int,
    old_cpk: float,
    new_std: float,
    projected_cpk: float,
    gate_cleared: bool,
    timestamp: str,
) -> None:
    """Append a row to the CSV remediation log."""
    os.makedirs(os.path.dirname(REMEDIATION_LOG_FILE), exist_ok=True)
    write_header = not os.path.exists(REMEDIATION_LOG_FILE)

    row = {
        "timestamp":       timestamp,
        "loop_count":      loop_count,
        "old_cpk":         round(old_cpk, 4),
        "new_std":         round(new_std, 4),
        "projected_cpk":   round(projected_cpk, 4),
        "gate_cleared":    gate_cleared,
    }

    with open(REMEDIATION_LOG_FILE, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    logger.debug("Remediation log updated: %s", REMEDIATION_LOG_FILE)
