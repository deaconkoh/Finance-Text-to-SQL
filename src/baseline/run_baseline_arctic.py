import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.baseline.baseline_runner import main
except ModuleNotFoundError:
    from baseline.baseline_runner import main


if __name__ == "__main__":
    if "--model" not in sys.argv:
        sys.argv[1:1] = ["--model", "arctic"]
    main()
