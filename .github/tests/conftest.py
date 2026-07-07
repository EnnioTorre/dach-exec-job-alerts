"""
Shared pytest fixtures/config for the job-alerts test suite.

Adds `.github/scripts` to sys.path so the scraper/ranker/issue modules
(which live outside a package) can be imported directly in tests.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
