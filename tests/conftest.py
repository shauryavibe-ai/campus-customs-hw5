"""Every test runs against a fresh copy of the original database and its own audit
trail in a temp folder, so tests never change data/campus_customs_new.db or
outputs/audit_trail.json."""

import shutil

import pytest

from mcp_server import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "campus_customs_test.db"
    shutil.copy(db.ORIGINAL_DB_PATH, path)
    monkeypatch.setenv("CAMPUS_DB_PATH", str(path))
    monkeypatch.setenv("AUDIT_TRAIL_PATH", str(tmp_path / "audit_trail.json"))  # never the real trail
    db.ensure_ops_tables()
    return path
