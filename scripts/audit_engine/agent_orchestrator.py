"""
agent_orchestrator.py
=====================
Main agentic workflow file for the Kaizen-Sigma Methodology.

Implements the Six Sigma "Check" phase (DMAIC — Control node) with an
autonomous sigma_analysis function and a Loop Guard to prevent infinite
remediation cycles.

Six Sigma Reference Limit:  3.4 DPMO  → Cpk ≥ 1.50 (long-term, 1.5σ shift)
Automated Verification Gate: Cpk ≥ 1.33 (Four-Sigma minimum production gate)
"""

import os
import sys
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Resolve project root so the module works whether run from repo root or
# scripts/audit_engine/.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.auditor.logic import calculate_six_sigma_metrics, calculate_pce_metrics
from src.auditor.trend import sigma_trend_analysis, append_cpk_to_history, load_trend_state
from src.auditor.spc import run_spc_analysis

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Specification limits (process-as-code — single source of truth)
USL: float = 103.0
LSL: float = 97.0
TARGET: float = 100.0

# Six Sigma quality gates
DPMO_LIMIT: float = 3.4            # World-class Six Sigma defect rate
CPK_SIX_SIGMA: float = 1.50        # Corresponds to 3.4 DPMO (long-term)
CPK_PRODUCTION_GATE: float = 1.33  # Four-Sigma minimum gate (current audit)

# Loop Guard — maximum autonomous remediation iterations before escalation
MAX_REMEDIATION_LOOPS: int = 3

# Persistent loop-guard state file (survives individual run_sigma_analysis calls)
LOOP_GUARD_STATE_FILE = os.path.join(_REPO_ROOT, "data", "loop_guard_state.json")

# ---------------------------------------------------------------------------
# Loop Guard Utilities
# ---------------------------------------------------------------------------

def _load_loop_guard_state() -> dict:
    """Load the persisted loop-guard counter, or return a fresh state."""
    if os.path.exists(LOOP_GUARD_STATE_FILE):
        try:
            with open(LOOP_GUARD_STATE_FILE, "r") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"loop_count": 0, "last_run": None, "status": "idle"}


def _save_loop_guard_state(state: dict) -> None:
    """Persist the loop-guard counter to disk."""
    os.makedirs(os.path.dirname(LOOP_GUARD_STATE_FILE), exist_ok=True)
    with open(LOOP_GUARD_STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)


def reset_loop_guard() -> None:
    """
    Public helper — call after a successful remediation cycle to reset the
    loop counter.  Also invoked automatically when the Check phase passes.
    """
    state = {"loop_count": 0, "last_run": datetime.utcnow().isoformat(), "status": "idle"}
    _save_loop_guard_state(state)
    logger.info("Loop Guard reset. Counter cleared.")


# ---------------------------------------------------------------------------
# Core: sigma_analysis — Six Sigma "Check" Phase
# ---------------------------------------------------------------------------

def sigma_analysis(
    measurements: np.ndarray | None = None,
    data_file: str | None = None,
    usl: float = USL,
    lsl: float = LSL,
    target: float = TARGET,
    cpk_gate: float = CPK_PRODUCTION_GATE,
) -> dict:
    """
    Execute the Six Sigma "Check" phase (DMAIC Control node).

    Evaluates process capability against the 3.4 DPMO world-class benchmark
    and the configurable production gate (default Cpk ≥ 1.33).

    Parameters
    ----------
    measurements : np.ndarray, optional
        Pre-loaded measurement array.  Mutually exclusive with *data_file*.
    data_file : str, optional
        Path to a CSV file containing a ``measurement`` column.
        Defaults to ``data/raw_measurements.csv`` relative to the repo root.
    usl, lsl, target : float
        Upper / Lower Specification Limits and nominal target.
    cpk_gate : float
        Minimum Cpk for the PASSED verdict.  Defaults to CPK_PRODUCTION_GATE.

    Returns
    -------
    dict
        Keys:
        - ``status``        : ``"PASSED"`` | ``"INTERVENTION_REQUIRED"``
        - ``cpk``           : measured Cpk (float)
        - ``sigma_level``   : measured sigma level (float)
        - ``dpmo_estimate`` : estimated DPMO (float)
        - ``loop_count``    : current remediation loop iteration (int)
        - ``loop_guard_triggered`` : True if MAX_REMEDIATION_LOOPS exceeded
        - ``metrics``       : full dict from calculate_six_sigma_metrics()
        - ``timestamp``     : ISO-8601 UTC string

    Raises
    ------
    ValueError
        If neither *measurements* nor *data_file* is resolvable.
    RuntimeError
        If the Loop Guard limit is exceeded (escalation signal).
    """

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    if measurements is None:
        resolved_file = data_file or os.path.join(_REPO_ROOT, "data", "raw_measurements.csv")
        if not os.path.exists(resolved_file):
            raise ValueError(f"Data file not found: {resolved_file}")
        df = pd.read_csv(resolved_file)
        if "measurement" not in df.columns:
            raise ValueError("CSV must contain a 'measurement' column.")
        measurements = df["measurement"].values

    measurements = np.asarray(measurements, dtype=float)
    if measurements.size == 0:
        raise ValueError("measurements array is empty.")

    # ------------------------------------------------------------------
    # 2. Calculate Six Sigma metrics & Run SPC
    # ------------------------------------------------------------------
    metrics = calculate_six_sigma_metrics(measurements, usl, lsl, target)
    cpk = metrics["cpk"]
    sigma_level = metrics["sigma_level"]
    
    spc_result = run_spc_analysis(measurements, mean=target, std=metrics["std"])

    # Estimate DPMO from sigma level (simplified Zst → DPMO table approximation)
    # Using standard normal CDF approximation: DPMO ≈ (1 − Φ(sigma_level)) × 1_000_000
    from scipy.stats import norm  # lazy import — optional dependency
    dpmo_estimate = (1.0 - norm.cdf(sigma_level)) * 1_000_000

    # ------------------------------------------------------------------
    # 3. Loop Guard — load and validate iteration counter
    # ------------------------------------------------------------------
    guard_state = _load_loop_guard_state()
    loop_count = guard_state.get("loop_count", 0)
    loop_guard_triggered = False

    if cpk < cpk_gate:
        # Process is NOT capable — increment remediation counter
        loop_count += 1
        guard_state["loop_count"] = loop_count
        guard_state["last_run"] = datetime.utcnow().isoformat()

        if loop_count > MAX_REMEDIATION_LOOPS:
            guard_state["status"] = "escalated"
            _save_loop_guard_state(guard_state)
            loop_guard_triggered = True
            logger.critical(
                "LOOP GUARD TRIGGERED — %d consecutive failed remediation "
                "cycles (max=%d).  Escalating for human review.",
                loop_count,
                MAX_REMEDIATION_LOOPS,
            )
            # Return escalation result (do not raise — let caller decide)
        else:
            guard_state["status"] = "intervention_required"
            _save_loop_guard_state(guard_state)
            logger.warning(
                "sigma_analysis: INTERVENTION REQUIRED — Cpk=%.4f < gate=%.4f "
                "(loop %d/%d).  DPMO estimate: %.2f (limit: %.1f).",
                cpk,
                cpk_gate,
                loop_count,
                MAX_REMEDIATION_LOOPS,
                dpmo_estimate,
                DPMO_LIMIT,
            )
    else:
        # Process is capable — reset loop guard
        guard_state["status"] = "passed"
        _save_loop_guard_state(guard_state)
        reset_loop_guard()
        logger.info(
            "sigma_analysis: PASSED — Cpk=%.4f ≥ gate=%.4f, "
            "Sigma Level=%.2f, DPMO estimate=%.2f.",
            cpk,
            cpk_gate,
            sigma_level,
            dpmo_estimate,
        )

    # ------------------------------------------------------------------
    # 4. Determine overall status
    # ------------------------------------------------------------------
    if cpk >= cpk_gate:
        status = "PASSED"
    elif loop_guard_triggered:
        status = "LOOP_GUARD_ESCALATION"
    else:
        status = "INTERVENTION_REQUIRED"

    # ------------------------------------------------------------------
    # 4a. Auto-remediation — trigger on INTERVENTION_REQUIRED
    # ------------------------------------------------------------------
    remediation_result = None
    if status == "INTERVENTION_REQUIRED":
        try:
            from scripts.audit_engine.auto_remediation import auto_remediate
            remediation_result = auto_remediate(
                loop_count=loop_count,
                current_cpk=cpk,
            )
            logger.info(
                "Auto-remediation applied: projected_cpk=%.4f, gate_cleared=%s.",
                remediation_result["projected_cpk"],
                remediation_result["gate_cleared"],
            )
        except Exception as exc:  # pragma: no cover — optional dependency
            logger.warning("Auto-remediation skipped: %s", exc)

    # ------------------------------------------------------------------
    # 4b. Append Cpk to rolling history and run trend analysis
    # ------------------------------------------------------------------
    trend_result = None
    try:
        cpk_history_file = os.path.join(_REPO_ROOT, "data", "cpk_history.csv")
        append_cpk_to_history(cpk, log_file=cpk_history_file)
        # Run trend only if we have enough history
        import pandas as _pd
        if os.path.exists(cpk_history_file):
            _hist_len = len(_pd.read_csv(cpk_history_file))
            if _hist_len >= 2:
                trend_result = sigma_trend_analysis(log_file=cpk_history_file)
                logger.info(
                    "Sigma trend: trajectory=%s | mean_cpk=%.4f | drift_alert=%s.",
                    trend_result["trajectory"],
                    trend_result["rolling_mean_cpk"],
                    trend_result["drift_alert"],
                )
    except Exception as exc:  # pragma: no cover
        logger.warning("Trend analysis skipped: %s", exc)

    # ------------------------------------------------------------------
    # 5. Build result payload
    # ------------------------------------------------------------------
    result = {
        "status": status,
        "cpk": cpk,
        "sigma_level": sigma_level,
        "dpmo_estimate": dpmo_estimate,
        "dpmo_limit": DPMO_LIMIT,
        "cpk_gate": cpk_gate,
        "loop_count": loop_count,
        "loop_guard_triggered": loop_guard_triggered,
        "metrics": metrics,
        "spc": spc_result,
        "trend": trend_result,
        "remediation": remediation_result,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    return result


# ---------------------------------------------------------------------------
# Orchestrator entry-point
# ---------------------------------------------------------------------------

def run_orchestrator() -> None:
    """
    Entry-point for the autonomous agentic workflow.

    Runs sigma_analysis and exits with:
      0 — PASSED
      1 — INTERVENTION_REQUIRED
      2 — LOOP_GUARD_ESCALATION
    """
    logger.info("=" * 60)
    logger.info("KAIZEN-SIGMA AGENT ORCHESTRATOR — Six Sigma CHECK Phase")
    logger.info("=" * 60)

    try:
        result = sigma_analysis()
    except (ValueError, RuntimeError) as exc:
        logger.error("Orchestrator error: %s", exc)
        sys.exit(2)

    status = result["status"]
    logger.info("Final status: %s", status)
    logger.info(
        "  Cpk=%.4f | Sigma Level=%.2f | DPMO estimate=%.2f",
        result["cpk"],
        result["sigma_level"],
        result["dpmo_estimate"],
    )

    exit_codes = {"PASSED": 0, "INTERVENTION_REQUIRED": 1, "LOOP_GUARD_ESCALATION": 2}
    sys.exit(exit_codes.get(status, 1))


if __name__ == "__main__":
    run_orchestrator()
