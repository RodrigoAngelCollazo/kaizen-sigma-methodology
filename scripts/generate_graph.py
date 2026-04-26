import os
import sys

# Add repo root to python path to allow importing from scripts
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.audit_engine.workflow import app

def main():
    png_data = app.get_graph().draw_mermaid_png()
    output_path = os.path.join(_REPO_ROOT, "workflow_graph.png")
    with open(output_path, "wb") as f:
        f.write(png_data)
    print(f"Graph saved to {output_path}")

if __name__ == "__main__":
    main()
