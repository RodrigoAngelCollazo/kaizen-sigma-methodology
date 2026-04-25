"""
test_phase6.py
==============
TDD test suite for Phase 6 components:
  - sigma_trend_analysis()  (src/auditor/trend.py)
  - auto_remediate()        (scripts/audit_engine/auto_remediation.py)

Run with:
    pytest tests/test_phase6.py -v --tb=short
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.auditor.trend as trend_mod
import scripts.audit_engine.auto_remediation as remediation_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_state_paths(tmp_path, monkeypatch):
    """Redirect all state/log file paths to tmp_path so tests are isolated."""
    monkeypatch.setattr(trend_mod,      "TREND_STATE_FILE",     str(tmp_path / "trend_state.json"))
    monkeypatch.setattr(remediation_mod, "RAW_MEASUREMENTS_FILE", str(tmp_path / "raw_measurements.csv"))
    monkeypatch.setattr(remediation_mod, "REMEDIATION_LOG_FILE",  str(tmp_path / "remediation_log.csv"))
    yield tmp_path


def _cpk_series(n: int = 40, base: float = 1.6, slope: float = 0.0, noise: float = 0.05) -> np.ndarray:
    """Generate a synthetic Cpk time series."""
    rng = np.random.default_rng(seed=7)
    x = np.arange(n, dtype=float)
    return base + slope * x + rng.normal(0, noise, n)


# ===========================================================================
# sigma_trend_analysis tests
# ===========================================================================

class TestSigmaTrendAnalysis:

    # ---- Happy-path --------------------------------------------------------

    def test_returns_required_keys(self, tmp_path):
        cpk = _cpk_series(n=40, base=1.6)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk)
        required = {
            "trajectory", "drift_alert", "rolling_mean_cpk", "rolling_min_cpk",
            "rolling_max_cpk", "slope", "window_size", "below_gate_count",
            "gate_breach_pct", "timestamp",
        }
        assert required.issubset(result.keys())

    def test_improving_trajectory(self):
        # Rising Cpk → slope > 0.002 → IMPROVING
        cpk = _cpk_series(n=50, base=1.0, slope=0.03, noise=0.005)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk)
        assert result["trajectory"] == "IMPROVING", f"Got {result['trajectory']}, slope={result['slope']:.5f}"

    def test_degrading_trajectory(self):
        # Falling Cpk → slope < -0.002 → DEGRADING
        cpk = _cpk_series(n=50, base=2.0, slope=-0.04, noise=0.005)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk)
        assert result["trajectory"] == "DEGRADING", f"Got {result['trajectory']}, slope={result['slope']:.5f}"

    def test_stable_trajectory(self):
        cpk = _cpk_series(n=40, base=1.6, slope=0.0, noise=0.001)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk)
        assert result["trajectory"] == "STABLE"

    def test_drift_alert_fires_on_drop(self):
        # Second half significantly lower than first → drift_alert = True
        half = 20
        cpk = np.concatenate([
            np.full(half, 1.8),   # first half — high
            np.full(half, 1.4),   # second half — drop of 0.4 > DRIFT_ALERT_DELTA (0.10)
        ])
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk, window=40)
        assert result["drift_alert"] is True

    def test_drift_alert_not_fired_on_stable(self):
        cpk = _cpk_series(n=40, base=1.6, slope=0.0, noise=0.001)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk)
        assert result["drift_alert"] is False

    def test_below_gate_count_correct(self):
        gate = trend_mod.CPK_GATE  # 1.33
        # Half below gate, half above
        cpk = np.concatenate([np.full(15, 1.0), np.full(15, 1.6)])
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk, window=30)
        assert result["below_gate_count"] == 15
        assert pytest.approx(result["gate_breach_pct"], abs=0.1) == 50.0

    def test_window_clamps_to_available_data(self):
        cpk = _cpk_series(n=10)
        result = trend_mod.sigma_trend_analysis(cpk_series=cpk, window=50)
        assert result["window_size"] == 10  # only 10 available

    def test_state_persisted_to_file(self, tmp_path):
        cpk = _cpk_series(n=20, base=1.6)
        trend_mod.sigma_trend_analysis(cpk_series=cpk)
        state_path = trend_mod.TREND_STATE_FILE
        assert os.path.exists(state_path)
        loaded = json.loads(Path(state_path).read_text())
        assert "trajectory" in loaded

    # ---- Error cases -------------------------------------------------------

    def test_raises_on_empty_series(self):
        with pytest.raises(ValueError, match="empty"):
            trend_mod.sigma_trend_analysis(cpk_series=np.array([]))

    def test_raises_on_missing_file(self):
        with pytest.raises(ValueError, match="not found"):
            trend_mod.sigma_trend_analysis(log_file="/nonexistent/cpk_history.csv")

    def test_raises_on_missing_cpk_column(self, tmp_path):
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_text("value\n1.0\n2.0\n")
        with pytest.raises(ValueError, match="'cpk' column"):
            trend_mod.sigma_trend_analysis(log_file=str(bad_csv))

    def test_load_trend_state_default_when_no_file(self):
        state = trend_mod.load_trend_state()
        assert state["trajectory"] == "UNKNOWN"
        assert state["drift_alert"] is False


class TestAppendCpkToHistory:

    def test_creates_file_on_first_call(self, tmp_path):
        log = str(tmp_path / "hist.csv")
        trend_mod.append_cpk_to_history(1.45, log_file=log)
        assert os.path.exists(log)

    def test_appends_multiple_rows(self, tmp_path):
        log = str(tmp_path / "hist.csv")
        for v in [1.2, 1.4, 1.6]:
            trend_mod.append_cpk_to_history(v, log_file=log)
        df = pd.read_csv(log)
        assert len(df) == 3
        assert list(df["cpk"]) == [1.2, 1.4, 1.6]

    def test_header_written_only_once(self, tmp_path):
        log = str(tmp_path / "hist.csv")
        for _ in range(5):
            trend_mod.append_cpk_to_history(1.5, log_file=log)
        with open(log) as fh:
            lines = fh.readlines()
        # Header + 5 data rows = 6 lines
        assert lines[0].strip() == "timestamp,cpk"
        assert len(lines) == 6


# ===========================================================================
# auto_remediate tests
# ===========================================================================

class TestAutoRemediate:

    # ---- Dry-run (no file I/O) ---------------------------------------------

    def test_dry_run_no_files_written(self, tmp_path):
        result = remediation_mod.auto_remediate(loop_count=1, current_cpk=0.9, dry_run=True)
        assert result["dry_run"] is True
        assert result["remediation_applied"] is False
        assert not os.path.exists(str(tmp_path / "raw_measurements.csv"))
        assert not os.path.exists(str(tmp_path / "remediation_log.csv"))

    def test_dry_run_returns_projected_cpk(self):
        result = remediation_mod.auto_remediate(loop_count=1, current_cpk=0.9, dry_run=True)
        assert "projected_cpk" in result
        assert result["projected_cpk"] > 0

    # ---- Live run ----------------------------------------------------------

    def test_live_run_writes_csv(self, tmp_path):
        result = remediation_mod.auto_remediate(loop_count=1, current_cpk=0.9)
        assert result["remediation_applied"] is True
        assert os.path.exists(str(tmp_path / "raw_measurements.csv"))

    def test_live_run_appends_log(self, tmp_path):
        remediation_mod.auto_remediate(loop_count=1, current_cpk=0.9)
        remediation_mod.auto_remediate(loop_count=2, current_cpk=0.8)
        log_path = str(tmp_path / "remediation_log.csv")
        df = pd.read_csv(log_path)
        assert len(df) == 2
        assert list(df["loop_count"]) == [1, 2]

    def test_tighter_std_per_iteration(self):
        r1 = remediation_mod.auto_remediate(loop_count=1, current_cpk=1.0, dry_run=True)
        r2 = remediation_mod.auto_remediate(loop_count=2, current_cpk=1.0, dry_run=True)
        assert r2["new_std"] < r1["new_std"], "Each loop should apply tighter variance"

    def test_higher_loops_yield_better_cpk(self):
        """Later remediation iterations should produce higher projected Cpk."""
        r1 = remediation_mod.auto_remediate(loop_count=1, current_cpk=0.8, dry_run=True)
        r3 = remediation_mod.auto_remediate(loop_count=3, current_cpk=0.8, dry_run=True)
        assert r3["projected_cpk"] >= r1["projected_cpk"]

    def test_gate_cleared_flag(self):
        # Loop 3 should tighten std enough to clear the gate
        r3 = remediation_mod.auto_remediate(loop_count=3, current_cpk=0.8, dry_run=True)
        assert r3["gate_cleared"] is True, (
            f"Expected gate cleared at loop 3, projected_cpk={r3['projected_cpk']:.4f}"
        )

    def test_std_hard_floor(self):
        # Extreme loop count should not produce degenerate (near-zero) std
        r = remediation_mod.auto_remediate(loop_count=100, current_cpk=0.1, dry_run=True)
        assert r["new_std"] >= 0.05

    def test_result_contains_all_keys(self):
        r = remediation_mod.auto_remediate(loop_count=1, current_cpk=0.9, dry_run=True)
        expected = {
            "remediation_applied", "old_cpk", "new_std", "projected_cpk",
            "gate_cleared", "loop_count", "timestamp", "dry_run",
        }
        assert expected.issubset(r.keys())
