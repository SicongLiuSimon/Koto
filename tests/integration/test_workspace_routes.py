# -*- coding: utf-8 -*-
"""
Integration tests for workspace-related endpoints in web/app.py:
- GET  /api/workspace/<path:filepath> — serve workspace files
- GET  /api/workspace — list workspace files
- POST /api/open-workspace — open workspace folder
- POST /api/open-file — open file natively
- GET  /api/browse — browse folders
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def client():
    """Create Flask test client from the monolith web app."""
    os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
    os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
    os.environ.pop("SENTRY_DSN", None)

    from web.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _check(resp, ok_status=(200,)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


# ── GET /api/workspace ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestListWorkspace:
    def test_list_returns_200(self, client):
        resp = client.get("/api/workspace")
        assert resp.status_code == 200

    def test_list_returns_files_array(self, client):
        data = _check(client.get("/api/workspace"))
        assert "files" in data
        assert isinstance(data["files"], list)


# ── GET /api/workspace/<path:filepath> ───────────────────────────────────────


@pytest.mark.integration
class TestWorkspaceFile:
    def test_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/workspace/nonexistent_file_xyz.txt")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client):
        """Path traversal attempts (../) should be rejected with 403 or 404."""
        resp = client.get("/api/workspace/../../etc/passwd")
        assert resp.status_code in (403, 404)

    def test_path_traversal_encoded_blocked(self, client):
        """Encoded path traversal should also be blocked."""
        resp = client.get("/api/workspace/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (403, 404)

    def test_serve_existing_file(self, client):
        """Create a file in workspace and verify it can be served."""
        from web.app import WORKSPACE_DIR

        test_filename = "_test_integration_workspace.txt"
        test_path = os.path.join(WORKSPACE_DIR, test_filename)
        try:
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("workspace file content")
            resp = client.get(f"/api/workspace/{test_filename}")
            assert resp.status_code == 200
            assert b"workspace file content" in resp.data
        finally:
            try:
                os.unlink(test_path)
            except (PermissionError, OSError):
                pass  # Windows file locking — will be cleaned up later

    def test_subdirectory_traversal_blocked(self, client):
        """Traversal within subdirectories should be blocked."""
        resp = client.get("/api/workspace/subdir/../../../etc/hosts")
        assert resp.status_code in (403, 404)


# ── POST /api/open-file ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestOpenFile:
    def test_missing_filepath_returns_400(self, client):
        resp = client.post("/api/open-file", json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_empty_filepath_returns_400(self, client):
        resp = client.post("/api/open-file", json={"filepath": ""})
        assert resp.status_code == 400

    def test_nonexistent_file_returns_404(self, client):
        resp = client.post("/api/open-file", json={"filepath": "nonexistent_xyz.txt"})
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["success"] is False

    def test_path_traversal_returns_403(self, client):
        resp = client.post("/api/open-file", json={"filepath": "../../etc/passwd"})
        assert resp.status_code in (403, 404)

    def test_method_not_allowed_get(self, client):
        """GET /api/open-file should return 405."""
        resp = client.get("/api/open-file")
        assert resp.status_code == 405


# ── POST /api/open-workspace ─────────────────────────────────────────────────


@pytest.mark.integration
class TestOpenWorkspace:
    @patch("subprocess.Popen")
    def test_open_workspace_returns_success(self, mock_popen, client):
        mock_popen.return_value = None
        resp = client.post("/api/open-workspace")
        data = resp.get_json()
        assert data["success"] is True
        assert "path" in data


# ── GET /api/browse ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestBrowseFolders:
    def test_browse_root_returns_200(self, client):
        resp = client.get("/api/browse?path=C:\\")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "folders" in data
        assert isinstance(data["folders"], list)

    def test_browse_nonexistent_path(self, client):
        resp = client.get("/api/browse?path=Z:\\nonexistent_dir_xyz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data

    def test_browse_returns_parent(self, client):
        resp = client.get("/api/browse?path=C:\\Windows")
        data = resp.get_json()
        if "error" not in data:
            assert "parent" in data
            assert "current" in data
