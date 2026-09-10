#!/usr/bin/env python3
"""Bootstrap installer. Coding agents run this; humans should not copy files.

    python3 scripts/install.py --agent auto
    python3 scripts/install.py --agent grok --scope user
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

try:
    from self_orch.install import main
except ImportError:
    sys.stderr.write(
        "self-orch sources not found. Clone https://github.com/PLEE93/self-orch.git "
        "and run scripts/install.py from that checkout.\n"
    )
    raise SystemExit(2)

if __name__ == "__main__":
    raise SystemExit(main())
