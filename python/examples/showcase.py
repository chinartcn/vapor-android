"""本机/CI 运行 Showcase（无头或原生都行）。

    PYTHONPATH=python python3 python/examples/showcase.py
    VAPOR_DEMO_FRAMES=3 PYTHONPATH=python python3 python/examples/showcase.py
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "protocol", "showcase", "lib"))

import showcase_app  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(showcase_app.main())
