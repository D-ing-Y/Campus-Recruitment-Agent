"""Compatibility wrapper for the packaged CLI entrypoint."""

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from campus_job_agent.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
