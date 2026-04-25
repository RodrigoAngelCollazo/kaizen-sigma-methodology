import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

# ---------------------------------------------------------------------------
# Resolve repo root so this script works from any working directory
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

LOOP_GUARD_STATE_FILE = os.path.join(_REPO_ROOT, "data", "loop_guard_state.json")
CPK_HISTORY_FILE      = os.path.join(_REPO_ROOT, "data", "cpk_history.csv")
KAIZEN_LOG            = os.path.join(_REPO_ROOT, "methodology", "kaizen_events", "continuous_improvement_log.csv")
OUTPUT_DIR            = os.path.join(_REPO_ROOT, "data", "visuals")

CPK_GATE      = 1.33
CPK_SIX_SIGMA = 1.50
MUDA_LIMIT    = 5.0
PCE_TARGET    = 90.0

PALETTE = {
    "tomato":     "#E05C5C",
    "seagreen":   "#3D9970",
    "dodgerblue": "#2E86C1",
    "gold":       "#D4AC0D",
    "purple":     "#7D3C98",
    "bg":         "#1A1A2E",
    "panel":      "#16213E",
    "text":       "#E8E8E8",
    "grid":       "#2A2A4A",
}


def _load_loop_guard() -> dict:
    if os.path.exists(LOOP_GUARD_STATE_FILE):
        try:
            with open(LOOP_GUARD_STATE_FILE) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"loop_count": 0, "status": "idle", "last_run": None}


def generate_dashboard():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(KAIZEN_LOG):
        print(f"Error: {KAIZEN_LOG} not found. Run kaizen_data_gen.py first.")
        return

    df_kaizen = pd.read_csv(KAIZEN_LOG)
    df_cpk    = pd.read_csv(CPK_HISTORY_FILE) if os.path.exists(CPK_HISTORY_FILE) else None
    guard     = _load_loop_guard()

    # ------------------------------------------------------------------
    # Figure layout — 5 panels (3 original + Cpk trend + Loop Guard)
    # ------------------------------------------------------------------
    plt.rcParams.update({
        "figure.facecolor":  PALETTE["bg"],
        "axes.facecolor":    PALETTE["panel"],
        "axes.edgecolor":    PALETTE["grid"],
        "axes.labelcolor":   PALETTE["text"],
        "xtick.color":       PALETTE["text"],
        "ytick.color":       PALETTE["text"],
        "text.color":        PALETTE["text"],
        "grid.color":        PALETTE["grid"],
        "grid.alpha":        0.5,
        "font.family":       "DejaVu Sans",
    })

    fig = plt.figure(figsize=(14, 22))
    gs  = GridSpec(5, 1, figure=fig, hspace=0.45)

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    ax3 = fig.add_subplot(gs[2])
    ax4 = fig.add_subplot(gs[3])
    ax5 = fig.add_subplot(gs[4])

    # ------------------------------------------------------------------
    # Panel 1 — Muda (Waste) Reduction Curve
    # ------------------------------------------------------------------
    ax1.plot(df_kaizen.index, df_kaizen["Waste_Muda_Hrs"],
             color=PALETTE["tomato"], linewidth=2, label="Muda (Waste)", zorder=3)
    ax1.fill_between(df_kaizen.index, df_kaizen["Waste_Muda_Hrs"],
                     color=PALETTE["tomato"], alpha=0.15)
    ax1.axhline(y=MUDA_LIMIT, color="#FF6B6B", linestyle="--", alpha=0.7,
                label=f"Muda Limit ({MUDA_LIMIT}h)", linewidth=1.5)
    ax1.set_title("🗑️  Waste (Muda) Reduction Curve", fontsize=13, fontweight="bold", pad=8)
    ax1.set_ylabel("Hours")
    ax1.legend(framealpha=0.2)
    ax1.grid(True)

    # ------------------------------------------------------------------
    # Panel 2 — PCE % Progression
    # ------------------------------------------------------------------
    ax2.plot(df_kaizen.index, df_kaizen["PCE_Percent"],
             color=PALETTE["seagreen"], linewidth=2, label="PCE %", zorder=3)
    ax2.fill_between(df_kaizen.index, df_kaizen["PCE_Percent"],
                     color=PALETTE["seagreen"], alpha=0.10)
    ax2.axhline(y=PCE_TARGET, color="#A9DFBF", linestyle="--", alpha=0.7,
                label=f"{PCE_TARGET}% Target", linewidth=1.5)
    ax2.set_title("📈  Process Cycle Efficiency (PCE) Progression", fontsize=13, fontweight="bold", pad=8)
    ax2.set_ylabel("Efficiency %")
    ax2.legend(framealpha=0.2)
    ax2.grid(True)

    # ------------------------------------------------------------------
    # Panel 3 — Process Stability (Lead Time + Rolling Mean)
    # ------------------------------------------------------------------
    ax3.plot(df_kaizen.index, df_kaizen["Total_Lead_Time"],
             color=PALETTE["dodgerblue"], alpha=0.35, linewidth=1, label="Total Lead Time")
    ax3.plot(df_kaizen.index, df_kaizen["Total_Lead_Time"].rolling(window=10).mean(),
             color=PALETTE["dodgerblue"], linewidth=2.5, label="10-Event Moving Avg", zorder=3)
    ax3.axhline(y=8.5, color="#AED6F1", linestyle="-", alpha=0.4,
                label="Standard Mean (8.5h)", linewidth=1.5)
    ax3.set_title("⚙️  Process Stability & Variance Tightening", fontsize=13, fontweight="bold", pad=8)
    ax3.set_ylabel("Hours")
    ax3.set_xlabel("Kaizen Iteration")
    ax3.legend(framealpha=0.2)
    ax3.grid(True)

    # ------------------------------------------------------------------
    # Panel 4 — Rolling Cpk Trend
    # ------------------------------------------------------------------
    if df_cpk is not None and len(df_cpk) >= 2:
        cpk_vals = df_cpk["cpk"].values
        x_cpk    = np.arange(len(cpk_vals))
        ax4.plot(x_cpk, cpk_vals, color=PALETTE["gold"], linewidth=2,
                 label="Cpk (per run)", zorder=3, marker="o", markersize=4)

        # Rolling mean (window=min(30, n))
        window = min(30, len(cpk_vals))
        rolling_cpk = pd.Series(cpk_vals).rolling(window=window).mean()
        ax4.plot(x_cpk, rolling_cpk, color=PALETTE["gold"], linewidth=2.5,
                 linestyle="--", alpha=0.8, label=f"{window}-Run Rolling Mean")

        # Gate lines
        ax4.axhline(y=CPK_GATE, color=PALETTE["tomato"], linestyle="--", linewidth=1.5,
                    alpha=0.8, label=f"Production Gate (Cpk≥{CPK_GATE})")
        ax4.axhline(y=CPK_SIX_SIGMA, color=PALETTE["seagreen"], linestyle=":",
                    linewidth=1.5, alpha=0.7, label=f"Six Sigma Target (Cpk≥{CPK_SIX_SIGMA})")

        # Shade below gate
        ax4.fill_between(x_cpk, 0, CPK_GATE, color=PALETTE["tomato"], alpha=0.07)

        # Trajectory annotation
        if len(cpk_vals) >= 4:
            slope = float(np.polyfit(x_cpk[-10:], cpk_vals[-10:], 1)[0])
            traj  = "⬆ IMPROVING" if slope > 0.002 else ("⬇ DEGRADING" if slope < -0.002 else "➡ STABLE")
            color = PALETTE["seagreen"] if "IMPROVING" in traj else (PALETTE["tomato"] if "DEGRADING" in traj else PALETTE["gold"])
            ax4.annotate(f"Trajectory: {traj}", xy=(0.98, 0.88), xycoords="axes fraction",
                         fontsize=10, fontweight="bold", color=color,
                         ha="right", bbox=dict(boxstyle="round,pad=0.3", fc=PALETTE["panel"], alpha=0.8))
    else:
        ax4.text(0.5, 0.5, "No Cpk history yet.\nRun agent_orchestrator.py to populate.",
                 ha="center", va="center", transform=ax4.transAxes,
                 color=PALETTE["text"], fontsize=11, alpha=0.7)

    ax4.set_title("📉  Rolling Cpk Trend (30-Run Window)", fontsize=13, fontweight="bold", pad=8)
    ax4.set_ylabel("Cpk")
    ax4.set_xlabel("Pipeline Run #")
    ax4.set_ylim(bottom=0)
    ax4.legend(framealpha=0.2)
    ax4.grid(True)

    # ------------------------------------------------------------------
    # Panel 5 — Loop Guard Status Indicator
    # ------------------------------------------------------------------
    ax5.set_xlim(0, 10)
    ax5.set_ylim(0, 4)
    ax5.axis("off")
    ax5.set_title("🔒  Loop Guard Status Monitor", fontsize=13, fontweight="bold", pad=8)

    status   = guard.get("status", "idle")
    loop_cnt = guard.get("loop_count", 0)
    last_run = guard.get("last_run", "N/A")

    # Colour and label per state
    state_config = {
        "idle":                   (PALETTE["seagreen"], "✅  IDLE",               "No active intervention. Process is in control."),
        "passed":                 (PALETTE["seagreen"], "✅  PASSED",             "Latest Check phase passed. Loop counter reset."),
        "intervention_required":  (PALETTE["gold"],     "⚠️   INTERVENTION",     f"Loop {loop_cnt}/3 — Remediation sub-agent active."),
        "escalated":              (PALETTE["tomato"],   "🚨  ESCALATED",          "Loop Guard breached. Human review required."),
    }
    sc_color, sc_label, sc_desc = state_config.get(
        status, (PALETTE["text"], f"UNKNOWN ({status})", "")
    )

    # Status badge
    badge = mpatches.FancyBboxPatch((0.5, 2.0), 9.0, 1.5, boxstyle="round,pad=0.15",
                                     linewidth=2, edgecolor=sc_color,
                                     facecolor=PALETTE["panel"])
    ax5.add_patch(badge)
    ax5.text(5.0, 2.75, sc_label, ha="center", va="center",
             fontsize=16, fontweight="bold", color=sc_color)
    ax5.text(5.0, 2.2, sc_desc, ha="center", va="center",
             fontsize=10, color=PALETTE["text"], alpha=0.85)

    # Loop counter bar
    max_loops = 3
    bar_colors = [PALETTE["seagreen"] if i >= loop_cnt else PALETTE["tomato"] for i in range(max_loops)]
    for i, c in enumerate(bar_colors):
        rect = mpatches.FancyBboxPatch((0.5 + i * 3.1, 0.3), 2.7, 1.2,
                                        boxstyle="round,pad=0.1",
                                        linewidth=1, edgecolor=PALETTE["grid"],
                                        facecolor=c if i < loop_cnt else PALETTE["panel"])
        ax5.add_patch(rect)
        label_color = PALETTE["tomato"] if i < loop_cnt else PALETTE["text"]
        ax5.text(1.85 + i * 3.1, 0.95, f"Loop {i+1}",
                 ha="center", va="center", fontsize=10, color=label_color, fontweight="bold")

    ax5.text(5.0, 0.05, f"Last run: {last_run}", ha="center", va="bottom",
             fontsize=8, color=PALETTE["text"], alpha=0.6)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    fig.suptitle("🛡️  Kaizen-Sigma Integrated Audit Dashboard",
                 fontsize=16, fontweight="bold", y=0.995, color=PALETTE["text"])

    output_path = os.path.join(OUTPUT_DIR, "kaizen_dashboard.png")
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=PALETTE["bg"])
    print(f"Enhanced Kaizen Dashboard generated: {output_path}")
    plt.close()


if __name__ == "__main__":
    generate_dashboard()

