import os
import json
from datetime import datetime
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END

# Define KaizenState
class KaizenState(TypedDict):
    history: List[str]
    metrics: Dict[str, Any]
    process_status: str

# Define nodes
def sigma_analysis(state: KaizenState) -> KaizenState:
    # Dummy implementation representing the analysis
    # In a real scenario, this would call the logic in agent_orchestrator.py
    if state.get("process_status") == "human_intervention_required":
        return state
    state["process_status"] = "passed"
    return state

def human_review_node(state: KaizenState) -> KaizenState:
    print("[ALERT] Human Intervention Required: Max Kaizen attempts reached. Manual Root Cause Analysis triggered.")
    state["process_status"] = "pending_human_review"
    return state

def logger_node(state: KaizenState) -> KaizenState:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/audit_log_{timestamp}.json"
    with open(log_file, "w") as f:
        json.dump({
            "history": state.get("history", []),
            "metrics": state.get("metrics", {})
        }, f, indent=2)
    return state

# Define routing
def route_after_analysis(state: KaizenState) -> str:
    if state.get("process_status") == "human_intervention_required":
        return "human_review_node"
    return "logger_node"

# Build graph
workflow = StateGraph(KaizenState)

workflow.add_node("sigma_analysis", sigma_analysis)
workflow.add_node("human_review_node", human_review_node)
workflow.add_node("logger_node", logger_node)

workflow.set_entry_point("sigma_analysis")

workflow.add_conditional_edges(
    "sigma_analysis",
    route_after_analysis,
    {
        "human_review_node": "human_review_node",
        "logger_node": "logger_node"
    }
)

workflow.add_edge("human_review_node", "logger_node")
workflow.add_edge("logger_node", END)

app = workflow.compile()
