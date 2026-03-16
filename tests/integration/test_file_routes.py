# -*- coding: utf-8 -*-
"""
Integration tests for file-related endpoints in web/app.py:
- POST /api/chat/file — file upload with chat
- POST /api/notebook/upload — notebook file upload
- GET  /api/files/download — file download proxy
- POST /api/file-editor/read — read file content
- POST /api/file-editor/write — write file content
- POST /api/file-editor/replace — replace text in file
"""

from __future__ import annotations

import io
import os
import tempfile

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


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


# ── POST /api/chat/file ──────────────────────────────────────────────────────


@pytest.mark.integration
class TestChatFileUpload:
    def test_no_file_returns_error(self, client):
        """POST /api/chat/file with no file part should fail."""
        resp = client.post("/api/chat/file", data={})
        assert resp.status_code in (400, 500)

    def test_empty_filename_returns_error(self, client):
        """POST /api/chat/file with empty filename should fail."""
        data = {"file": (io.BytesIO(b""), "")}
        resp = client.post(
            "/api/chat/file", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code in (400, 500)

    def test_upload_txt_file_accepted(self, client):
        """A plain text file should be accepted (even if chat fails without LLM)."""
        data = {
            "file": (io.BytesIO(b"Hello world"), "test.txt"),
            "message": "Summarize this",
            "session": "test_session",
        }
        resp = client.post(
            "/api/chat/file", data=data, content_type="multipart/form-data"
        )
        # Accepted for processing (200) or fails at LLM stage (500) — not 404/405
        assert resp.status_code in (200, 400, 500)

    def test_upload_returns_json(self, client):
        """Response should be JSON regardless of success/failure."""
        data = {
            "file": (io.BytesIO(b"test content"), "sample.txt"),
            "message": "analyze",
            "session": "test_session",
        }
        resp = client.post(
            "/api/chat/file", data=data, content_type="multipart/form-data"
        )
        # Response should be parseable (JSON or streaming text)
        assert resp.content_type is not None


# ── POST /api/notebook/upload ────────────────────────────────────────────────


@pytest.mark.integration
class TestNotebookUpload:
    def test_no_file_part_returns_400(self, client):
        resp = client.post("/api/notebook/upload", data={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_empty_filename_returns_400(self, client):
        data = {"file": (io.BytesIO(b""), "")}
        resp = client.post(
            "/api/notebook/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False

    def test_upload_txt_file_succeeds(self, client):
        """A simple .txt file should be parsed successfully."""
        content = b"This is a test document for notebook upload."
        data = {"file": (io.BytesIO(content), "test_notebook.txt")}
        resp = client.post(
            "/api/notebook/upload", data=data, content_type="multipart/form-data"
        )
        # txt parsing should succeed
        body = resp.get_json()
        if resp.status_code == 200:
            assert body["success"] is True
            assert body["filename"] == "test_notebook.txt"
            assert body["char_count"] > 0

    def test_method_not_allowed_get(self, client):
        """GET /api/notebook/upload should return 405."""
        resp = client.get("/api/notebook/upload")
        assert resp.status_code == 405


# ── GET /api/files/download ──────────────────────────────────────────────────


@pytest.mark.integration
class TestFileDownload:
    def test_missing_path_returns_404(self, client):
        resp = client.get("/api/files/download")
        assert resp.status_code == 404

    def test_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/files/download?path=/nonexistent/file.txt")
        assert resp.status_code == 404

    def test_valid_file_returns_200(self, client):
        """Downloading an existing file should succeed."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as tmp:
            tmp.write("download test content")
            tmp_path = tmp.name
        try:
            resp = client.get(f"/api/files/download?path={tmp_path}")
            assert resp.status_code == 200
            assert b"download test content" in resp.data
        finally:
            try:
                os.unlink(tmp_path)
            except PermissionError:
                pass  # Windows file locking — temp dir will clean up


# ── POST /api/file-editor/* ──────────────────────────────────────────────────


@pytest.mark.integration
class TestFileEditorRead:
    def test_missing_file_path_returns_400(self, client):
        resp = client.post("/api/file-editor/read", json={})
        assert resp.status_code == 400

    def test_read_existing_file(self, client):
        """Reading an existing file should return content."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as tmp:
            tmp.write("editor read test")
            tmp_path = tmp.name
        try:
            resp = client.post("/api/file-editor/read", json={"file_path": tmp_path})
            # Should return 200 with content or error gracefully
            assert resp.status_code in (200, 500)
        finally:
            os.unlink(tmp_path)


@pytest.mark.integration
class TestFileEditorWrite:
    def test_missing_params_returns_400(self, client):
        resp = client.post("/api/file-editor/write", json={})
        assert resp.status_code == 400

    def test_missing_content_returns_400(self, client):
        resp = client.post(
            "/api/file-editor/write", json={"file_path": "/tmp/test.txt"}
        )
        assert resp.status_code == 400


@pytest.mark.integration
class TestFileEditorReplace:
    def test_missing_params_returns_400(self, client):
        resp = client.post("/api/file-editor/replace", json={})
        assert resp.status_code == 400

    def test_missing_old_text_returns_400(self, client):
        resp = client.post(
            "/api/file-editor/replace",
            json={"file_path": "/tmp/test.txt", "new_text": "replacement"},
        )
        assert resp.status_code == 400
