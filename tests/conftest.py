import sys
from pathlib import Path

# Add src to sys.path for test imports
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
