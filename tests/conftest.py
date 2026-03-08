#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared pytest fixtures for Koto test suite.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
import pytest

def _root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def _koto_tmp_db(tmp_path_factory):
    """Isolated temp DB dir for the whole session."""
    tmpdir = str(tmp_path_factory.mktemp("koto_db"))
    os.environ["KOTO_DB_DIR"] = tmpdir
    return tmpdir


@pytest.fixture(scope="session")
def app(_koto_tmp_db):
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from flask import Flask
    from app.api.skill_routes import skill_bp
    from app.api.job_routes import job_bp

    application = Flask(__name__)
    application.register_blueprint(skill_bp)
    application.register_blueprint(job_bp)
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def binding_id(client):
    """Create a test intent binding and return its ID for dependent tests."""
    resp = client.post(
        "/api/skills/concise_mode/bindings/intent",
        json={"patterns": ["测试极简", "最短回答"], "auto_disable_after_turns": 1},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    return data["binding"]["binding_id"]
