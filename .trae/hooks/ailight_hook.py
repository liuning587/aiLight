#!/usr/bin/env python3
"""TRAE IDE project hook entry -> tools.ailight_hook"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.ailight_hook import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
