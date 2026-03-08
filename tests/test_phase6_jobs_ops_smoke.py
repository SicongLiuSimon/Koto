#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 6 smoke test for jobs, triggers, bindings, and ops APIs.

This script runs in an isolated temporary config directory by setting KOTO_DB_DIR
before importing the runtime modules. It validates:

1. Recommended skill bindings can be bootstrapped and listed
2. Recommended trigger templates can be bootstrapped and listed
3. Ops health/metrics endpoints respond
4. A manual trigger fire path returns a task id
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from flask import Flask


def build_app() -> Flask:
    app = Flask(__name__)
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from app.api.skill_routes import skill_bp
    from app.api.job_routes import job_bp
    from app.api.ops_routes import ops_bp

    app.register_blueprint(skill_bp)
    app.register_blueprint(job_bp)
    app.register_blueprint(ops_bp)
    return app


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="koto_phase6_")
    try:
        os.environ["KOTO_DB_DIR"] = tmp
        app = build_app()
        client = app.test_client()

        print("[1] Bootstrapping recommended skill bindings...")
        resp = client.post("/api/skills/bindings/bootstrap", json={"force": True})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        bindings_bootstrap = resp.get_json()
        print(bindings_bootstrap)

        resp = client.get("/api/skills/bindings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        bindings_data = resp.get_json()
        assert 5 <= bindings_data["count"] <= 12, bindings_data
        print(f"intent bindings: {bindings_data['count']}")

        print("[2] Bootstrapping recommended triggers...")
        resp = client.post("/api/jobs/triggers/bootstrap", json={"force": True})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        triggers_bootstrap = resp.get_json()
        print(triggers_bootstrap)

        resp = client.get("/api/jobs/triggers/templates")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        templates_data = resp.get_json()
        assert templates_data["total"] >= 3, templates_data
        print(f"trigger templates: {templates_data['total']}")

        resp = client.get("/api/jobs/triggers")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        triggers_data = resp.get_json()
        assert triggers_data["total"] >= 3, triggers_data
        print(f"registered triggers: {triggers_data['total']}")

        print("[3] Checking ops endpoints...")
        resp = client.get("/api/ops/health")
        assert resp.status_code in (200, 503), resp.get_data(as_text=True)
        print(f"health status code: {resp.status_code}")

        resp = client.get("/api/ops/metrics")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        metrics = resp.get_json()
        assert "timestamp" in metrics, metrics
        print("metrics ok")

        print("[4] Firing a manual trigger...")
        from app.core.jobs.job_runner import get_job_runner

        get_job_runner().register_handler("smoke_noop", lambda ctx: "smoke-ok")
        webhook_trigger = client.post(
            "/api/jobs/triggers",
            json={
                "name": "smoke-webhook",
                "trigger_type": "webhook",
                "job_type": "smoke_noop",
                "job_payload": {"message": "noop"},
                "enabled": True,
            },
        )
        assert webhook_trigger.status_code == 201, webhook_trigger.get_data(as_text=True)
        trigger_id = webhook_trigger.get_json()["data"]["trigger_id"]

        resp = client.post(f"/api/jobs/triggers/{trigger_id}/fire")
        assert resp.status_code == 202, resp.get_data(as_text=True)
        fire_data = resp.get_json()
        assert fire_data["data"]["task_id"], fire_data
        print(f"manual trigger task_id: {fire_data['data']['task_id']}")

        print("\nPhase 6 smoke test passed.")
        return 0
    finally:
        time.sleep(1.0)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
