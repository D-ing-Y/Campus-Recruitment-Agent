"""Local interpreter tweaks for repository commands."""

import os
import sys
from pathlib import Path


if "pytest" in Path(sys.argv[0]).name or any("pytest" in arg for arg in sys.argv):
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
