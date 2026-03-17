"""Test that Swagger docs are accessible."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class TestApiDocs:
    @pytest.fixture(autouse=True)
    def setup(self):
        from web.app import app

        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_apidocs_accessible(self):
        resp = self.client.get("/apidocs/")
        assert resp.status_code == 200

    def test_apispec_json(self):
        resp = self.client.get("/apispec.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "info" in data
        assert data["info"]["title"] == "Koto API"

    def test_api_paths_documented(self):
        resp = self.client.get("/apispec.json")
        data = resp.get_json()
        paths = data.get("paths", {})
        # At least health endpoints should be documented
        assert len(paths) > 0
