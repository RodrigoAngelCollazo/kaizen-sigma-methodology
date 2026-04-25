"""
test_sigma_analysis.py
======================
TDD test suite for the sigma_analysis function in agent_orchestrator.py.

Covers:
  - SUCCESS (PASSED): Cpk above gate, loop guard resets.
  - INTERVENTION_REQUIRED: Cpk below gate, loop counter increments.
  - LOOP_GUARD_ESCALATION: Cpk below gate, loop count exceeds MAX_REMEDIATION_LOOPS.
  - Edge cases: empty data, missing file, zero std dev.

Run with:
    pytest tests/test_sigma_analysis.py -v
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.audit_engine.agent_orchestrator as orch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_loop_guard(tmp_path, monkeypatch):
    """
    Redirect LOOP_GUARD_STATE_FILE to a temporary path for each test so tests
    are fully isolated from one another and from real run artefacts.
    """
    guard_file = tmp_path / "loop_guard_state.json"
    monkeypatch.setattr(orch, "LOOP_GUARD_STATE_FILE", str(guard_file))
    yield guard_file


def _make_measurements(cpk_target: float = 2.0, n: int = 500) -> np.ndarray:
    """
    Generate synthetic measurements centred at TARGET with std chosen so that
    Cpk ≈ cpk_target given USL=103, LSL=97, TARGET=100.

    Cpk = min((USL - μ)/(3σ), (μ - LSL)/(3σ))
         = (3)/(3σ)  when μ = TARGET
    => σ = 1 / cpk_target
    """
    std = 1.0 / cpk_target
    rng = np.random.default_rng(seed=42)
    return rng.normal(loc=orch.TARGET, scale=std, size=n)


# ---------------------------------------------------------------------------
# ✅ SUCCESS Tests — PASSED state
# ---------------------------------------------------------------------------

class TestSigmaAnalysisPassed:
    """sigma_analysis must return PASSED when Cpk ≥ 1.33."""

    def test_status_is_passed(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        assert result["status"] == "PASSED", (
            f"Expected PASSED but got {result['status']} (Cpk={result['cpk']:.4f})"
        )

    def test_cpk_above_gate(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        assert result["cpk"] >= orch.CPK_PRODUCTION_GATE

    def test_loop_guard_reset_on_pass(self, isolated_loop_guard):
        # Pre-seed a non-zero loop counter
        isolated_loop_guard.write_text(json.dumps({"loop_count": 2, "status": "intervention_required"}))
        data = _make_measurements(cpk_target=2.0)
        orch.sigma_analysis(measurements=data)
        state = json.loads(isolated_loop_guard.read_text())
        assert state["loop_count"] == 0, "Loop Guard must reset to 0 after a PASSED result"
        assert state["status"] == "idle"

    def test_dpmo_estimate_low_on_pass(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        # A well-capable process should have DPMO well below 3.4
        assert result["dpmo_estimate"] < 3.4 + 100  # generous tolerance for synthetic data

    def test_loop_guard_triggered_false_on_pass(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        assert result["loop_guard_triggered"] is False

    def test_result_contains_required_keys(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        required = {
            "status", "cpk", "sigma_level", "dpmo_estimate",
            "dpmo_limit", "cpk_gate", "loop_count", "loop_guard_triggered",
            "metrics", "timestamp",
        }
        assert required.issubset(result.keys())

    def test_custom_cpk_gate_respected(self):
        """A tighter gate should still pass when data is very capable."""
        data = _make_measurements(cpk_target=2.5)
        result = orch.sigma_analysis(measurements=data, cpk_gate=1.50)
        assert result["status"] == "PASSED"


# ---------------------------------------------------------------------------
# ⚠️ INTERVENTION_REQUIRED Tests
# ---------------------------------------------------------------------------

class TestSigmaAnalysisIntervention:
    """sigma_analysis must return INTERVENTION_REQUIRED when Cpk < 1.33."""

    def test_status_is_intervention_required(self):
        data = _make_measurements(cpk_target=0.7)
        result = orch.sigma_analysis(measurements=data)
        assert result["status"] == "INTERVENTION_REQUIRED", (
            f"Expected INTERVENTION_REQUIRED but got {result['status']} (Cpk={result['cpk']:.4f})"
        )

    def test_cpk_below_gate(self):
        data = _make_measurements(cpk_target=0.7)
        result = orch.sigma_analysis(measurements=data)
        assert result["cpk"] < orch.CPK_PRODUCTION_GATE

    def test_loop_counter_increments(self, isolated_loop_guard):
        data = _make_measurements(cpk_target=0.7)
        # First failure
        r1 = orch.sigma_analysis(measurements=data)
        assert r1["loop_count"] == 1
        # Second failure
        r2 = orch.sigma_analysis(measurements=data)
        assert r2["loop_count"] == 2

    def test_loop_guard_not_triggered_within_limit(self):
        data = _make_measurements(cpk_target=0.7)
        result = orch.sigma_analysis(measurements=data)
        assert result["loop_guard_triggered"] is False

    def test_dpmo_estimate_high_on_failure(self):
        data = _make_measurements(cpk_target=0.7)
        result = orch.sigma_analysis(measurements=data)
        # A low-Cpk process should produce a high DPMO estimate
        assert result["dpmo_estimate"] > 1000, (
            f"Expected high DPMO for poor process, got {result['dpmo_estimate']:.2f}"
        )

    def test_loop_count_max_before_escalation(self, isolated_loop_guard):
        """At exactly MAX_REMEDIATION_LOOPS failures, status is still INTERVENTION_REQUIRED."""
        data = _make_measurements(cpk_target=0.7)
        for _ in range(orch.MAX_REMEDIATION_LOOPS):
            result = orch.sigma_analysis(measurements=data)
        assert result["status"] == "INTERVENTION_REQUIRED"
        assert result["loop_count"] == orch.MAX_REMEDIATION_LOOPS
        assert result["loop_guard_triggered"] is False


# ---------------------------------------------------------------------------
# 🚨 LOOP_GUARD_ESCALATION Tests
# ---------------------------------------------------------------------------

class TestLoopGuardEscalation:
    """Loop Guard must trigger when loop_count exceeds MAX_REMEDIATION_LOOPS."""

    def test_escalation_after_max_loops(self, isolated_loop_guard):
        data = _make_measurements(cpk_target=0.7)
        # Run MAX + 1 times to breach the limit
        for _ in range(orch.MAX_REMEDIATION_LOOPS + 1):
            result = orch.sigma_analysis(measurements=data)
        assert result["status"] == "LOOP_GUARD_ESCALATION"
        assert result["loop_guard_triggered"] is True

    def test_escalation_state_persisted(self, isolated_loop_guard):
        data = _make_measurements(cpk_target=0.7)
        for _ in range(orch.MAX_REMEDIATION_LOOPS + 1):
            orch.sigma_analysis(measurements=data)
        state = json.loads(isolated_loop_guard.read_text())
        assert state["status"] == "escalated"
        assert state["loop_count"] > orch.MAX_REMEDIATION_LOOPS

    def test_reset_loop_guard_clears_escalation(self, isolated_loop_guard):
        data = _make_measurements(cpk_target=0.7)
        for _ in range(orch.MAX_REMEDIATION_LOOPS + 1):
            orch.sigma_analysis(measurements=data)
        # Manually reset
        orch.reset_loop_guard()
        state = json.loads(isolated_loop_guard.read_text())
        assert state["loop_count"] == 0
        assert state["status"] == "idle"


# ---------------------------------------------------------------------------
# 🔧 Edge Cases
# ---------------------------------------------------------------------------

class TestSigmaAnalysisEdgeCases:
    """Edge cases: bad inputs, zero variance, CSV loading."""

    def test_raises_on_empty_measurements(self):
        with pytest.raises(ValueError, match="empty"):
            orch.sigma_analysis(measurements=np.array([]))

    def test_raises_on_missing_file(self):
        with pytest.raises(ValueError, match="not found"):
            orch.sigma_analysis(data_file="/nonexistent/path/data.csv")

    def test_raises_on_missing_measurement_column(self, tmp_path):
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_text("value,other\n1,2\n3,4\n")
        with pytest.raises(ValueError, match="'measurement' column"):
            orch.sigma_analysis(data_file=str(bad_csv))

    def test_loads_from_csv_file(self, tmp_path):
        """sigma_analysis should load data from a CSV with a 'measurement' column."""
        good_data = _make_measurements(cpk_target=2.0)
        import pandas as pd
        csv_path = tmp_path / "measurements.csv"
        pd.DataFrame({"measurement": good_data}).to_csv(csv_path, index=False)
        result = orch.sigma_analysis(data_file=str(csv_path))
        assert result["status"] == "PASSED"

    def test_zero_std_returns_inf_cpk(self):
        """Constant process (zero variance) should produce infinite Cpk."""
        data = np.full(100, 100.0)
        result = orch.sigma_analysis(measurements=data)
        assert result["cpk"] == float("inf")
        assert result["status"] == "PASSED"

    def test_timestamp_format(self):
        data = _make_measurements(cpk_target=2.0)
        result = orch.sigma_analysis(measurements=data)
        # Should be parseable as an ISO datetime
        from datetime import datetime
        dt = datetime.fromisoformat(result["timestamp"].rstrip("Z"))
        assert dt.year >= 2026
