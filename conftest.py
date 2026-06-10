"""Pytest bootstrap: put project root and src/ on the import path so tests can
import both `config` and the `healthcompanion` package without installation.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (ROOT, ROOT / "src"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
