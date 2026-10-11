"""Local Pipeline E filesystem anchors; CLI/tests select their canonical run root."""
from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[3]
RUNS = Path(os.getenv("PIPELINE_E_RUNS_DIR", "data/generated_outputs/pipeline_e"))
