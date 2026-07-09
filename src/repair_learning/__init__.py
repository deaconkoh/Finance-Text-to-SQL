"""Optional learned repair baselines for fixed-verifier ablations."""

from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_LLAMA31_8B_BASE_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
