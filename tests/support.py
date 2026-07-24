from __future__ import annotations

import os
from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(
    os.environ.get("A0_TEST_PROJECT_ROOT", PLUGIN_ROOT.parents[2])
).resolve()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
