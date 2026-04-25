"""
trend.py
========
Sigma trend analysis module — Phase 6.

Tracks Cpk drift over a configurable rolling window (default: 30 days / events)
and classifies the process trajectory as IMPROVING, STABLE, or DEGRADING.

Used by:
  - agent_orchestrator.py (post-analysis enrichment)
  - kaizen_dashboard.py (Cpk trend panel)
  - antigravity.yaml (trend-gate step)
"""

from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_WINDOW = 30          # events / rows to include in each rolling window
CPK_GATE = 1.33              # production gate — mirrors agent_orchestrator.py
CPK_SIX_SIGMA = 1.50         # world-class target
DRIFT_ALERT_DELTA = 0.10     # Cpk drop ≥ this value in one window = alert
TREND_STATE_FILE = "data/sigma_trend_state.json"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sigma_trend_analysis(
    cpk_series: Optional[np.ndarray | list] = None,
    log_file: str = "data/cpk_history.csv",
    window: int = DEFAULT_WINDOW,
) -> dict:
    """
    Analyse Cpk drift over a rolling *window* of measurements.

    Parameters
    ----------
    cpk_series : array-like, optional
        Pre-computed Cpk values in chronological order.
        If *None*, the function loads from *log_file*.
    log_file : str
        Path to a CSV with a ``cpk`` column (chronological, oldest-first).
        Created/appended automatically by the orchestrator.
    window : int
        Number of most-recent events to include in the rolling analysis.

    Returns
    -------
    dict
        Keys:
        - ``trajectory``       : ``"IMPROVING"`` | ``"STABLE"`` | ``"DEGRADING"``
        - ``drift_alert``      : bool — True if Cpk dropped ≥ DRIFT_ALERT_DELTA
        - ``rolling_mean_cpk`` : float — mean Cpk over the window
        - ``rolling_min_cpk``  : float — worst Cpk in the window
        - ``rolling_max_cpk``  : float — best Cpk in the window
        - ``slope``            : float — linear regression slope (Cpk / event)
        - ``window_size``      : int — actual number of events analysed
        - ``below_gate_count`` : int — events in window where Cpk < 1.33
        - ``gate_breach_pct``  : float — % of window events below gate
        - ``timestamp``        : ISO-8601 UTC string
    """
    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    if cpk_series is None:
        if not os.path.exists(log_file):
            raise ValueError(f"CPK history file not found: {log_file}")
        df = pd.read_csv(log_file)
        if "cpk" not in df.columns:
            raise ValueError("log_file must contain a 'cpk' column.")
        cpk_series = df["cpk"].values

    cpk_arr = np.asarray(cpk_series, dtype=float)
    if cpk_arr.size == 0:
        raise ValueError("cpk_series is empty.")

    # Clamp to the most recent *window* events
    window_data = cpk_arr[-window:]
    n = len(window_data)

    # ------------------------------------------------------------------
    # 2. Compute statistics
    # ------------------------------------------------------------------
    rolling_mean = float(np.mean(window_data))
    rolling_min  = float(np.min(window_data))
    rolling_max  = float(np.max(window_data))

    # Linear regression slope over the window (Cpk per event)
    x = np.arange(n, dtype=float)
    if n >= 2:
        slope = float(np.polyfit(x, window_data, 1)[0])
    else:
        slope = 0.0

    # Gate breach stats
    below_gate = int(np.sum(window_data < CPK_GATE))
    gate_breach_pct = round((below_gate / n) * 100, 2)

    # ------------------------------------------------------------------
    # 3. Classify trajectory
    # ------------------------------------------------------------------
    # Slope thresholds — empirically chosen for the synthetic data scale
    SLOPE_IMPROVING  =  0.002   # Cpk rising noticeably
    SLOPE_DEGRADING  = -0.002   # Cpk falling noticeably

    if slope >= SLOPE_IMPROVING:
        trajectory = "IMPROVING"
    elif slope <= SLOPE_DEGRADING:
        trajectory = "DEGRADING"
    else:
        trajectory = "STABLE"

    # Drift alert: compare first-half vs second-half mean
    drift_alert = False
    if n >= 4:
        half = n // 2
        first_half_mean  = float(np.mean(window_data[:half]))
        second_half_mean = float(np.mean(window_data[half:]))
        if (first_half_mean - second_half_mean) >= DRIFT_ALERT_DELTA:
            drift_alert = True
            logger.warning(
                "DRIFT ALERT — Cpk dropped %.4f points over the last %d events "
                "(%.4f → %.4f).",
                first_half_mean - second_half_mean,
                n,
                first_half_mean,
                second_half_mean,
            )

    # ------------------------------------------------------------------
    # 4. Persist trend state
    # ------------------------------------------------------------------
    state = {
        "trajectory": trajectory,
        "drift_alert": drift_alert,
        "rolling_mean_cpk": rolling_mean,
        "rolling_min_cpk": rolling_min,
        "rolling_max_cpk": rolling_max,
        "slope": slope,
        "window_size": n,
        "below_gate_count": below_gate,
        "gate_breach_pct": gate_breach_pct,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    os.makedirs(os.path.dirname(TREND_STATE_FILE) or ".", exist_ok=True)
    with open(TREND_STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)

    logger.info(
        "Sigma Trend — trajectory=%s | slope=%.5f | mean_cpk=%.4f | "
        "gate_breach=%.1f%% | drift_alert=%s",
        trajectory, slope, rolling_mean, gate_breach_pct, drift_alert,
    )

    return state


def append_cpk_to_history(cpk: float, log_file: str = "data/cpk_history.csv") -> None:
    """
    Append a new Cpk measurement to the rolling history CSV.
    Called by agent_orchestrator after each sigma_analysis run.
    """
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    row = pd.DataFrame([{"timestamp": datetime.utcnow().isoformat() + "Z", "cpk": cpk}])
    write_header = not os.path.exists(log_file)
    row.to_csv(log_file, mode="a", header=write_header, index=False)
    logger.debug("Appended Cpk=%.4f to %s", cpk, log_file)


def load_trend_state() -> dict:
    """Load the last persisted trend state, or return a neutral default."""
    if os.path.exists(TREND_STATE_FILE):
        try:
            with open(TREND_STATE_FILE) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "trajectory": "UNKNOWN",
        "drift_alert": False,
        "rolling_mean_cpk": None,
        "gate_breach_pct": None,
        "timestamp": None,
    }
