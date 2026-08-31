"""Shared FastAPI dependencies and repo-relative paths for the API routes."""

from __future__ import annotations

from pathlib import Path

from app.db.session import get_db  # noqa: F401  (re-exported for routes)

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw"
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth" / "ground_truth_matches.json"
