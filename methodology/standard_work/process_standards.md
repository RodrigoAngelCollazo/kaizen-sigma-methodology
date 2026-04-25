# Kaizen Standardized Work (Wedge)

_Ref: Page 254 - Preventing Quality Backslide_

## Current Standards:

1. **Target Process Mean:** 8.5 Hours
2. **Standard Deviation Limit:** 1.2 Hours
3. **Muda Threshold:** Any 'Waste_Muda_Hrs' > 5h triggers a Kaizen Blitz.

## PDCA Protocol:

- **Plan:** Analyze 'continuous_improvement_log.csv' for outliers.
- **Do:** Implement small-scale code refactors.
- **Check:** Verify 'PCE_Percent' increases in next 10 events.
- **Act:** Update this 'process_standards.md' file.

---

## Automated Verification Thresholds

_Ref: Six Sigma "Check" Phase — DMAIC Control Node (`agent_orchestrator.py`)_

These thresholds are enforced autonomously by the `sigma_analysis` function
and constitute the machine-readable quality gate for every CI/CD pipeline run.

### 3.4 DPMO World-Class Benchmark

| Parameter | Value | Rationale |
| :--- | :--- | :--- |
| **DPMO Limit** | **3.4** | Six Sigma world-class defect rate — 3.4 defects per million opportunities |
| **Equivalent Cpk** | **≥ 1.50** | Long-term capability index corresponding to 3.4 DPMO (accounts for 1.5σ mean shift) |
| **Equivalent Sigma Level** | **≥ 6.0σ** | Short-term process sigma; target for continuous improvement roadmap |

### Production Gate (Current Enforcement)

| Gate | Threshold | Description |
| :--- | :--- | :--- |
| **Minimum Cpk** | **≥ 1.33** | Four-Sigma production gate; triggers `INTERVENTION_REQUIRED` if breached |
| **DPMO Alert** | **> 64** | Corresponds to Cpk = 1.33; logged as a warning in the audit report |
| **Sigma Level Alert** | **< 4.0σ** | Secondary alert — process variance too high for sustained quality |

### Loop Guard Policy

The autonomous `sigma_analysis` node includes a **Loop Guard** to prevent
infinite remediation cycles. The policy is as follows:

| State | Condition | Agent Action |
| :--- | :--- | :--- |
| `PASSED` | Cpk ≥ 1.33 | Loop counter reset to 0; pipeline proceeds |
| `INTERVENTION_REQUIRED` | Cpk < 1.33 AND loops ≤ 3 | Loop counter incremented; remediation sub-agent triggered |
| `LOOP_GUARD_ESCALATION` | Cpk < 1.33 AND loops > 3 | **Human escalation required** — pipeline halted, alert issued |

- **Maximum Remediation Loops:** `3` consecutive failed Check cycles
- **Reset Condition:** A single `PASSED` verdict resets the counter to `0`
- **Escalation Artefact:** `data/loop_guard_state.json` records the current loop count and status

### Verification Checklist (Automated)

- [ ] `cpk >= 1.33` — enforced by `agent_orchestrator.py`
- [ ] `dpmo_estimate <= 64` — logged and exported to `GITHUB_ENV`
- [ ] `loop_count <= 3` — enforced by Loop Guard state machine
- [ ] `PCE_Percent` trending upward across last 10 Kaizen events
- [ ] `Waste_Muda_Hrs < 5.0h` — triggers Kaizen Blitz if exceeded
